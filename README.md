# Resume Enhance

Resume Enhance is an open-source resume builder designed to help you create professional, high-impact resumes. It simplifies the process of crafting clear and effective content.

You can import an existing resume or start from scratch. The application provides smart feedback to refine your bullet points using the **STAR method** (Situation, Task, Action, Result), ensuring your achievements are presented clearly. Once done, you can export your resume as a high-quality PDF.

## ✨ Key Features

*   **Smart Enhancement**: Automatically restructure your experience using the STAR format for maximum impact.
*   **Resume Dashboard**: Manage multiple resumes, duplicate specific versions, or delete old ones.
*   **PDF Import**: Upload your existing LinkedIn PDF resume to get started immediately.
*   **Live Preview**: See how your resume looks in real-time before exporting.
*   **PDF Export**: Download a professional, ATS-friendly PDF (currently supports FaangPath template).
*   **User Accounts**: Sign up, log in, and save your work securely.

## 📸 Product Tour

Here is what the application looks like:

### Landing Page
![Landing Page](screenshots/Screenshot%202026-02-06%20at%2000.20.25.png)

### Dashboard
Manage all your resumes in one place.
![Dashboard](screenshots/Screenshot%202026-02-06%20at%2000.22.22.png)

### Resume Editor
Edit your details and use AI to enhance your texts.
![Resume Editor](screenshots/Screenshot%202026-02-06%20at%2000.23.12.png)

### Live Preview
Check the final layout instantly.
![Live Preview](screenshots/Screenshot%202026-02-06%20at%2000.23.37.png)

### PDF Export
Get the final PDF document.
![PDF Export](screenshots/Screenshot%202026-02-06%20at%2000.23.56.png)

---

## 🚀 Getting Started

You can run this project using Docker (recommended) or manually in your terminal.

### Prerequisites

*   **OpenAI API Key**: You need a key to use the AI features.
*   **Docker**: For the easiest setup.
*   **Python 3.10+ & PostgreSQL**: If running manually.

### 1. Clone the Project

```bash
git clone https://github.com/koksalkapucuoglu/resume-enhance.git
cd resume-enhance
```

### 2. Configure Environment Variables

Create your `.env` file by copying the template:

```bash
cp .env.example .env
```

Open `.env` and update the values:

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key from OpenAI | `sk-proj-...` |
| `DEBUG` | Django debug mode | `True` (dev) or `False` (prod) |
| `SECRET_KEY` | Django secret key | Auto-generated for dev |
| `ALLOWED_HOSTS` | Allowed domain list | `localhost,127.0.0.1` |
| `POSTGRES_DB` | Database name | `postgres` |
| `POSTGRES_USER` | Database user | `postgres` |
| `POSTGRES_PASSWORD` | Database password | `postgres` (change in prod) |
| `EMAIL_HOST_USER` | SMTP email | `your_email@gmail.com` |
| `EMAIL_HOST_PASSWORD` | SMTP password | App password (not account password) |

For **production**, use `.env.prod.example` as your template and set `DEBUG=False`.

### 3. Option A: Run with Docker (Recommended)

This serves both the app and the database automatically.

```bash
# Build the project
make build

# Run the project
make run
```
The app will be available at [http://localhost:8000](http://localhost:8000).

To stop the app:
```bash
make down
```

### 4. Option B: Run Manually (Terminal)

If you prefer running without Docker, you need to have **PostgreSQL** running locally.

1.  **Create and Activate Virtual Environment**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run Migrations**
    Make sure your `DATABASE_URL` in `.env` points to your local Postgres database.
    ```bash
    python manage.py migrate
    ```

5.  **Run the Server**
    ```bash
    python manage.py runserver
    ```
    Visit [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## ☁️ Deployment (Hetzner / Self-Hosted)

We have migrated from Fly.io to a self-hosted setup on **Hetzner Cloud** (or any VPS) using Docker Compose.

**👉 [Read the Complete Deployment Guide](docs/HETZNER_GUIDE.md)**

This guide covers:
1.  Provisioning a cheap & powerful server (Ubuntu 24.04).
2.  Setting up Security (Firewall, SSH).
3.  Configuring DNS (`yourdomain.com`).
4.  Deploying with **Docker Compose** & **Caddy** (Automatic SSL).
5.  Setting up **GitHub Actions** for automatic deployment.

---

## 🗺️ Roadmap & Planned Features

We are constantly improving Resume Enhance. Here is what's coming next:

*   **Template Selection**: Choose from multiple resume designs (not just FaangPath).
*   **Parsing V2**: Better import quality for existing PDFs using newer AI models.
*   **Direct Download**: Download PDFs directly from the dashboard card.
*   **Feedback Loop**: A built-in form to report bugs or request features.
*   **Multi-language Support**: Support for Turkish and English interfaces.
*   **Rate Limiting**: Better protection against API abuse.

---

### License
Open Source.
