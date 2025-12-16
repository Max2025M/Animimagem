const express = require('express');
const multer = require('multer');
const fs = require('fs');
const path = require('path');
const ffmpeg = require('fluent-ffmpeg');

const app = express();
app.use(express.json());
app.use(express.static('public'));

const upload = multer({ dest: 'uploads/' });

const jobs = {}; // progresso em memória

app.post('/render', upload.single('image'), (req, res) => {
  const id = Date.now().toString();
  const input = req.file.path;
  const output = `renders/${id}.mp4`;

  if (!fs.existsSync('renders')) fs.mkdirSync('renders');

  jobs[id] = { progress: 0, done: false, url: null };

  ffmpeg(input)
    .outputOptions('-pix_fmt yuv420p')
    .videoCodec('libx264')
    .size('1280x720')
    .on('progress', p => {
      jobs[id].progress = Math.min(99, Math.round(p.percent || 0));
    })
    .on('end', () => {
      jobs[id].progress = 100;
      jobs[id].done = true;
      jobs[id].url = `/download/${id}`;

      // apaga tudo após 10 minutos
      setTimeout(() => {
        fs.unlinkSync(output);
        delete jobs[id];
      }, 10 * 60 * 1000);
    })
    .save(output);

  res.json({ id });
});

app.get('/progress/:id', (req, res) => {
  res.json(jobs[req.params.id] || { progress: 0 });
});

app.get('/download/:id', (req, res) => {
  res.download(`renders/${req.params.id}.mp4`);
});

app.listen(3000, () => console.log('Server on 3000'));
