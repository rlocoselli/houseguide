import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests/browser',use:{headless:true,launchOptions:process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{}},timeout:90000,workers:1});
