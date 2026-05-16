# Codeforge

**Built by the community, for the community.**

Codeforge is a community-driven software initiative dedicated to building affordable, cutting-edge technology that uplifts entire communities instead of just individuals.

## Vision

We believe technology should serve everyone. Codeforge creates and markets software projects that solve real problems at prices communities can actually afford — while using modern, proven tech stacks.

## How It Works

**Codeforge.show** is the mother brand and landing page. Each project lives under its own subdomain:

- `billbasket.codeforge.show` — Affordable billing software for small shops
- `xyz.codeforge.show` — Future services, each with its own dedicated site

## Our Values

- **Community First** — Every decision starts with: does this serve the community?
- **Affordable by Design** — No hidden fees, no premium tiers for basics
- **Cutting-Edge Tech** — Modern tools, proven practices
- **Open Collaboration** — Build together, contribute ideas
- **Real Impact** — Measure success by problems solved
- **Quality Without Compromise** — Affordable doesn't mean cheap

## Stack

- **Backend**: Django 4.2 (Python)
- **Frontend**: HTML5 + Tailwind CSS + Vanilla JS
- **Database**: SQLite (dev), swappable via env vars
- **Deployment**: GitHub → Auto-deploy pipeline (in progress)

## Getting Started

### Local Development

```bash
git clone https://github.com/Aslamkv/codeforge.git
cd codeforge
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit `http://localhost:8000`

### Configuration

Create a `.env` file in the project root:

```env
DEBUG=True
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///db.sqlite3
ALLOWED_HOSTS=localhost,127.0.0.1
```

### Admin

```bash
python manage.py createsuperuser
# Visit http://localhost:8000/admin
```

Edit `SiteSettings` to customize the homepage:
- Site name, tagline, description
- Email contact
- Technologies/stack

## Project Structure

```
codeforge/
├── codeforge/          # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── landing/            # Main app
│   ├── models.py       # SiteSettings model
│   ├── views.py        # Homepage view
│   ├── admin.py        # Admin customization
│   └── migrations/
├── templates/landing/  # HTML templates
│   └── home.html       # Tailwind-powered homepage
├── static/             # Static assets (future)
├── manage.py
└── requirements.txt
```

## Roadmap

### Phase 1 ✅ (Current)
- Community-focused branding
- Warm, accessible design with Tailwind
- Mission & values communication

### Phase 2 (Next)
- `Project` model for per-service subdomains
- Services section auto-populated from DB
- Groundwork for `billbasket.codeforge.show`

### Phase 3 (Future)
- GitHub → auto-deploy pipeline
- Contact form with email integration
- Image/logo uploads for projects
- SEO optimization & sitemap

## Contributing

We're building this as a community. If you believe in affordable, quality tech:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-idea`)
3. Commit with clear messages (`feat:`, `fix:`, `docs:`, etc.)
4. Push and open a PR

## License

See `LICENSE` file.

## Questions?

Reach out at contact@codeforge.show or open an issue.

---

**Codeforge** — Technology that uplifts communities. 🤝