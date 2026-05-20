# Flask Hello World

A minimal Python web app — perfect starting point for your hackathon.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

## Deploy to Render

1. Push this folder to a GitHub repo
2. Go to render.com → New → Web Service → connect your repo
3. Set:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app`
4. Click Deploy → get your live URL

## Project structure

```
flask-hello/
├── app.py                 ← Flask routes (your backend logic)
├── templates/
│   └── index.html         ← Frontend UI
├── requirements.txt       ← Dependencies
└── README.md
```

## How the frontend talks to Flask

The HTML page sends a POST request to `/api/greet`:

```javascript
const res = await fetch("/api/greet", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name: "Alice" })
});
const data = await res.json();  // { message: "Hello, Alice!", time: "14:32:01" }
```

Flask receives it in `app.py` and returns JSON. This is the pattern you'll use for your finance calculator too.
