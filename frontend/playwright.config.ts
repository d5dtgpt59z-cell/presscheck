import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',timeout:90000,workers:1,use:{baseURL:'http://127.0.0.1:3000',browserName:'chromium',viewport:{width:1512,height:1100}},reporter:[['list'],['json',{outputFile:'../docs/e2e-results.json'}]]});
