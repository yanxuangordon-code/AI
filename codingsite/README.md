# CodeSponge 🧽

A free online coding playground supporting Python, JavaScript, C++, and HTML.

## Project Structure

```
codingsite/
├── backend/        # Flask API (deploy to Render)
│   ├── app.py
│   ├── requirements.txt
│   └── render.yaml
└── frontend/       # Static site (deploy to GitHub Pages)
    ├── index.html
    ├── style.css
    └── app.js
```

## Deployment

### Step 1 — Deploy Backend to Render

1. Push the whole project to GitHub
2. Go to render.com and sign in
3. Click **New > Web Service**
4. Connect your GitHub repo
5. Set the **Root Directory** to `backend`
6. Render will auto-detect the `render.yaml` settings
7. Click **Deploy** — wait for it to finish
8. Copy your Render URL (e.g. `https://codingsite-backend.onrender.com`)

### Step 2 — Update Frontend with Backend URL

1. Open `frontend/app.js`
2. Replace this line at the top:
   ```js
   const BACKEND_URL = "https://your-backend.onrender.com";
   ```
   With your actual Render URL.

### Step 3 — Deploy Frontend to GitHub Pages

1. Go to your GitHub repo
2. Click **Settings > Pages**
3. Set source to **Deploy from a branch**
4. Set branch to `main`, folder to `/frontend`
5. Click **Save**
6. Your site will be live at `https://yourusername.github.io/your-repo-name`

## Running Locally

### Backend
```bash
cd backend
pip install -r requirements.txt
python app.py
```

### Frontend
Just open `frontend/index.html` in your browser.
But change BACKEND_URL in app.js to `http://localhost:5000` first.
