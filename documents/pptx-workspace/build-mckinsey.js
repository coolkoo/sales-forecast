/**
 * Build KFC Vietnam McKinsey-style presentation using html2pptx + PptxGenJS
 */
const path = require('path');
const fs = require('fs');
const PptxGenJS = require('/Users/jasonkoo/Library/Application Support/aura-workshop/bundled-deps/render-env/node_modules/pptxgenjs');

// html2pptx is at: skills/pptx/scripts/html2pptx.js
const html2pptx = require('/Users/jasonkoo/Library/Application Support/aura-workshop/skills/pptx/scripts/html2pptx.js');

const SLIDES_DIR = __dirname + '/slides';
const OUTPUT = __dirname + '/kfc-vietnam-sales-forecast-platform.pptx';

const slides = [
  { file: 'slide01-title.html', title: 'Title' },
  { file: 'slide02-problem.html', title: 'The Problem' },
  { file: 'slide03-cost.html', title: 'Cost of Delay' },
  { file: 'slide04-what.html', title: 'What We Built' },
  { file: 'slide05-beforeafter.html', title: 'Before vs After' },
  { file: 'slide06-pipeline.html', title: 'Pipeline' },
  { file: 'slide07-forecast.html', title: 'Forecast Engine' },
  { file: 'slide08-anomaly.html', title: 'Anomaly Detection' },
  { file: 'slide09-action.html', title: 'Closing the Loop' },
  { file: 'slide10-dashboard.html', title: 'Dashboard' },
  { file: 'slide11-infra.html', title: 'Infrastructure & Security' },
  { file: 'slide12-value.html', title: 'Business Value' },
  { file: 'slide13-roadmap.html', title: 'Road Ahead' },
  { file: 'slide14-summary.html', title: 'Summary & Next Steps' },
];

const TEAL = '004D43';

async function build() {
  const msg = 'Building McKinsey-style KFC Vietnam presentation...';
  console.log(msg);

  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'WIDE', width: 10, height: 5.625 });
  pptx.layout = 'WIDE';
  pptx.author = 'KFC Vietnam Operations & Finance';
  pptx.title = 'Faster Sales Anomaly Detection & Forecasting';
  pptx.subject = 'KFC Vietnam Sales Forecast Platform';

  for (let i = 0; i < slides.length; i++) {
    const slideDef = slides[i];
    const htmlPath = path.join(SLIDES_DIR, slideDef.file);
    console.log(' [' + (i+1) + '/' + slides.length + '] ' + slideDef.title + '...');

    try {
      // html2pptx: (htmlFilePath, pptxInstance, options) => { slide, placeholders }
      const result = await html2pptx(htmlPath, pptx, {
        width: 720,
        height: 405,
        baseDir: SLIDES_DIR,
      });

      const slideInstance = result.slide;

      // Override background for title/summary slides
      if (i === 0 || i === slides.length - 1) {
        slideInstance.background = { fill: TEAL };
      }

      // Add footer bar to content slides
      if (i > 0 && i < slides.length - 1) {
        slideInstance.addShape(pptx.ShapeType.rect, {
          x: 0, y: 5.25, w: 10, h: 0.375,
          fill: { color: 'F7F8F9' },
        });
        slideInstance.addText('KFC Vietnam Sales Anomaly Detection & Forecasting Platform', {
          x: 0.5, y: 5.27, w: 5, h: 0.3,
          fontSize: 6.5, fontFace: 'Arial', color: 'AAAAAA',
        });
        slideInstance.addText('Slide ' + (i+1) + ' of ' + slides.length, {
          x: 8, y: 5.27, w: 1.5, h: 0.3,
          fontSize: 6.5, fontFace: 'Arial', color: 'AAAAAA', align: 'right',
        });
      }

    } catch (err) {
      console.error(' [ERROR] Slide ' + (i+1) + ' "' + slideDef.title + '": ' + err.message);
      const slideInstance = pptx.addSlide();
      slideInstance.background = { fill: (i === 0 || i === slides.length - 1) ? TEAL : 'FFFFFF' };
      slideInstance.addText('Slide ' + (i+1) + ': ' + slideDef.title, {
        x: 0.5, y: 2, w: 9, h: 1,
        fontSize: 24, fontFace: 'Arial', color: (i === 0 || i === slides.length - 1) ? 'FFFFFF' : '333333',
      });
      slideInstance.addText('Error: ' + err.message, {
        x: 0.5, y: 3, w: 9, h: 0.5,
        fontSize: 14, fontFace: 'Arial', color: '999999',
      });
    }
  }

  await pptx.writeFile({ fileName: OUTPUT });
  console.log('Done! Created ' + OUTPUT);
  console.log('  ' + slides.length + ' slides');
}

build().catch(function(e) { console.error('Fatal:', e); process.exit(1); });
