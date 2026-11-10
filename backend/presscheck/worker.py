import json, logging, threading, time
from .storage import Session, Inspection, Asset, ROOT
from .models import InspectionRequest
from .engine import inspect
from .reports import pdf_report
log=logging.getLogger('presscheck.worker')

def run_one(ident):
    with Session() as db:
        job=db.get(Inspection,ident)
        if not job: return
        job.state='running'; db.commit()
        req=InspectionRequest.model_validate(job.request)
        a=db.get(Asset,req.reference).info; b=db.get(Asset,req.candidate).info
        try:
            folder=ROOT/'inspections'/ident
            result=inspect(ROOT/req.reference/f'page-{req.reference_page}.png',ROOT/req.candidate/f'page-{req.candidate_page}.png',folder,req.config)
            result.update(id=ident,files={'reference':a,'candidate':b},pages={'reference':req.reference_page,'candidate':req.candidate_page})
            pdf_report(result,folder)
            (folder/'report.json').write_text(json.dumps(result,indent=2))
            job.result=result; job.state='complete'; db.commit()
            log.info(json.dumps({'event':'inspection_complete','id':ident,'status':result['status'],'seconds':result['metrics']['processing_seconds']}))
        except Exception as exc:
            log.exception('Inspection failed %s',ident)
            job.state='error'; job.error=f'{type(exc).__name__}: {str(exc)[:400]}'; db.commit()

def loop(stop):
    # Exactly one worker per local deployment. Restart recovery requeues interrupted jobs.
    with Session() as db:
        for job in db.query(Inspection).filter_by(state='running'): job.state='queued'
        db.commit()
    while not stop.is_set():
        with Session() as db:
            row=db.query(Inspection).filter_by(state='queued').order_by(Inspection.created).first()
            ident=row.id if row else None
        if ident: run_one(ident)
        else: stop.wait(.3)

def start():
    stop=threading.Event(); thread=threading.Thread(target=loop,args=(stop,),daemon=True); thread.start(); return stop,thread
