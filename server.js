const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const { createCanvas, loadImage } = require('canvas');
const ffmpeg = require('fluent-ffmpeg');

const app = express();
app.use(express.json());
app.use(express.static('public'));

const upload = multer({ dest: 'uploads/' });

// Render MP4 diretamente via stream, sem salvar frames
app.post('/render', upload.single('image'), async (req, res) => {
  const { animation, duration, speed } = req.body;
  const imagePath = req.file.path;

  const canvasWidth = 1280;
  const canvasHeight = 720;
  const fps = 30;

  const img = await loadImage(imagePath);

  const canvas = createCanvas(canvasWidth, canvasHeight);
  const ctx = canvas.getContext('2d');

  // Configurar ffmpeg para receber frames via stdin
  const ff = ffmpeg()
    .input(canvas.createPNGStream())
    .inputFormat('image2pipe')
    .inputFPS(fps)
    .outputOptions('-pix_fmt yuv420p')
    .videoCodec('libx264')
    .format('mp4')
    .on('error', err => console.error(err));

  // Envia o MP4 como download diretamente
  res.setHeader('Content-Disposition', 'attachment; filename=video.mp4');
  ff.pipe(res);

  const totalFrames = Math.floor(duration * fps);
  const iw = img.width;
  const ih = img.height;

  let scale = Math.max(canvasWidth/iw, canvasHeight/ih)*1.3;
  let zoomFrom = scale, zoomTo = scale;

  let sx = (iw*scale - canvasWidth)/2;
  let sy = (ih*scale - canvasHeight)/2;
  let ex = sx, ey = sy;

  switch(animation){
    case 'lr': sx = 0; ex = iw*scale - canvasWidth; break;
    case 'rl': sx = iw*scale - canvasWidth; ex = 0; break;
    case 'tb': sy = 0; ey = ih*scale - canvasHeight; break;
    case 'bt': sy = ih*scale - canvasHeight; ey = 0; break;
    case 'zoomIn': zoomTo = scale*1.25; break;
    case 'zoomOut': zoomFrom = scale*1.25; break;
  }

  for (let i = 0; i < totalFrames; i++) {
    const t = i / totalFrames;
    const e = t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t+2,2)/2;
    const z = zoomFrom + (zoomTo - zoomFrom) * e;
    const x = sx + (ex - sx) * e;
    const y = sy + (ey - sy) * e;

    ctx.fillStyle = '#000';
    ctx.fillRect(0,0,canvasWidth,canvasHeight);
    ctx.drawImage(img, -x, -y, iw*z, ih*z);

    await new Promise(resolve => setTimeout(resolve, 1000/fps));
  }

  fs.unlinkSync(imagePath);
});
app.listen(3000, ()=>console.log('Server running on port 3000'));
