import json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

def pdf_report(result, folder: Path):
    dest=folder/'report.pdf'; styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Technical',fontName='Courier',fontSize=7,leading=10,wordWrap='CJK'))
    styles['BodyText'].fontSize=9; styles['BodyText'].leading=13
    story=[]
    def p(t, style='BodyText'):
        # Built-in Helvetica has no arbitrary Unicode glyph coverage.
        clean=str(t).encode('latin-1','replace').decode('latin-1')
        story.append(Paragraph(escape(clean),styles[style])); story.append(Spacer(1,7))
    def table(rows,widths=None):
        cells=[[Paragraph(escape(str(v)),styles['BodyText']) for v in row] for row in rows]
        t=Table(cells,colWidths=widths,hAlign='LEFT',repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8B83D')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#BCAD8D')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.append(t);story.append(Spacer(1,13))
    p('PRESSCHECK / MODEL PC-78','Title');p('BUILT BY MAGNUS - OPTICAL SYSTEMS DIVISION','Technical')
    p(result['status'],'Heading1');p('Inspection '+result['id'],'Technical');p(result['timestamp'],'Technical')
    p('Automated screening only. A PASS does not authorize production.')
    table([['Measurement','Value'],['Changed area',str(result['metrics']['changed_area_percent'])+'%'],['Mean CIEDE2000',result['metrics']['mean_delta_e']],['Registration support',str(round(result['registration']['confidence']*100,1))+'% (heuristic)'],['Processing time',str(result['metrics']['processing_seconds'])+' s']], [180,340])
    for role in ('reference','candidate'):
        asset=result['files'][role];page=asset['pages'][result['pages'][role]]
        p(role.upper()+': '+asset['name'],'Heading2')
        p('SHA-256: '+asset['sha256'],'Technical')
        p(f"Format {asset['format']} | bytes {asset['bytes']} | page {result['pages'][role]+1} | mode {page['mode']} | DPI {page.get('dpi')} | source size {page.get('width',page.get('points'))} x {page.get('height','points')} | preview {page['preview_width']} x {page['preview_height']}",'Technical')
        p('ICC: '+json.dumps(page.get('icc')),'Technical')
        for warning in asset['warnings']:p(warning,'Technical')
    p('Inspection configuration','Heading2');p(json.dumps(result['config']),'Technical')
    story.append(PageBreak());p('Visual evidence','Heading1')
    row=[]
    for name in ('reference.png','aligned.png'):
        im=Image(str(folder/name));im._restrictSize(248,330);row.append(im)
    story.append(Table([['APPROVED REFERENCE','REGISTERED CANDIDATE'],row],colWidths=[260,260]));story.append(Spacer(1,15))
    story.append(PageBreak());p('Difference overlay','Heading1');p('Orange boxes locate potential differences; overlays are screening evidence, not confirmed manufacturing causes.')
    im=Image(str(folder/'overlay.png'));im._restrictSize(510,330);story.append(im)
    story.append(PageBreak());p('Findings & recommendations','Heading1')
    if not result['findings']:p('No differences above configured thresholds.')
    for f in result['findings']:
        block_start=len(story)
        p(f"{f['id']} / {f['title']} / {f['severity'].upper()}",'Heading3')
        p(f['recommendation'])
        p('Bounds: '+str(f.get('bbox'))+' | area px: '+str(f.get('area_px','n/a'))+' | mean delta E: '+str(f.get('mean_delta_e','n/a')),'Technical')
        if f['kind']=='text':
            e=f['evidence'];p('Reference: '+e['reference']);p('Candidate: '+e['candidate']);p('OCR confidence: '+str(e['confidence']),'Technical')
        story[block_start:]=[KeepTogether(story[block_start:])]
    p('Packaging checks','Heading2')
    for g in result['geometry']:p(g['check']+': '+g['status'].upper()+' - '+g.get('note',g.get('reason','')))
    p('Color samples','Heading2')
    for sample in result['color_samples']:p(json.dumps(sample),'Technical')
    p('Barcode evidence','Heading2');p(json.dumps(result['barcode']),'Technical')
    p('AI interpretation','Heading2');p(json.dumps(result['ai']))
    p('Measurement limits','Heading2')
    for warning in result['warnings']:p(warning)
    p('Full structured evidence, OCR bounds, transforms, and source metadata are included in the companion JSON report. Software version '+result['version'],'Technical')
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor('#38291F'));canvas.line(36,35,559,35)
        canvas.setFont('Courier',7);canvas.drawString(36,23,'PRESSCHECK / '+result['id'][:12]);canvas.drawRightString(559,23,str(doc.page))
    SimpleDocTemplate(str(dest),title='PRESSCHECK Inspection '+result['id'],author='Built by Magnus',leftMargin=36,rightMargin=36,topMargin=35,bottomMargin=48).build(story,onFirstPage=footer,onLaterPages=footer)
    return dest
