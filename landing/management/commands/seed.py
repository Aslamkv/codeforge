"""
Management command: python manage.py seed

Populates the database with initial data for the Codeforge landing page.
Safe to run multiple times — uses get_or_create throughout.
"""

from django.core.management.base import BaseCommand

from landing.models import Feature, Project, SiteSettings, TeamMember, Technology


class Command(BaseCommand):
    help = "Seed the database with initial Codeforge content"

    def handle(self, *args, **options):
        self.stdout.write("Seeding Codeforge database...\n")

        self._seed_site_settings()
        self._seed_technologies()
        self._seed_team()
        self._seed_features()
        self._seed_projects()

        self.stdout.write(self.style.SUCCESS("\nDone. All records seeded."))

    # ------------------------------------------------------------------
    # Site settings
    # ------------------------------------------------------------------

    def _seed_site_settings(self):
        settings, created = SiteSettings.objects.get_or_create(
            pk=1,
            defaults={
                "site_name": "Codeforge",
                "tagline": "Built by the community, for the community.",
                "description": (
                    "Codeforge is a community of developers building affordable, "
                    "high-quality software for everyone. We believe technology should "
                    "uplift whole communities — not just the privileged few."
                ),
                "contact_email": "hello@codeforge.show",
                "github_url": "https://github.com/Aslamkv",
            },
        )
        self._log("SiteSettings", "Codeforge", created)

    # ------------------------------------------------------------------
    # Technologies
    # ------------------------------------------------------------------

    def _seed_technologies(self):
        technologies = [
            # BillBasket stack
            {"name": "Flutter", "category": "mobile", "description": "Cross-platform UI toolkit by Google", "order": 1},
            {"name": "Dart", "category": "mobile", "description": "Programming language behind Flutter", "order": 2},
            {"name": "Riverpod", "category": "mobile", "description": "State management for Flutter apps", "order": 3},
            {"name": "SQLite", "category": "database", "description": "Lightweight embedded database", "order": 1},
        ]

        for tech in technologies:
            obj, created = Technology.objects.get_or_create(
                name=tech["name"],
                defaults={
                    "category": tech["category"],
                    "description": tech["description"],
                    "order": tech["order"],
                    "is_active": True,
                },
            )
            self._log("Technology", tech["name"], created)

    # ------------------------------------------------------------------
    # Team
    # ------------------------------------------------------------------

    def _seed_team(self):
        members = [
            {
                "name": "Aslam",
                "role": "Founder & Lead Developer",
                "bio": "Building practical software for everyday businesses.",
                "avatar_initials": "AK",
                "github_url": "https://github.com/Aslamkv",
                "order": 1,
            },
        ]

        for member in members:
            obj, created = TeamMember.objects.get_or_create(
                name=member["name"],
                defaults={
                    "role": member["role"],
                    "bio": member["bio"],
                    "avatar_initials": member["avatar_initials"],
                    "github_url": member["github_url"],
                    "order": member["order"],
                    "is_active": True,
                },
            )
            self._log("TeamMember", member["name"], created)

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    def _seed_features(self):
        features = [
            {
                "title": "Open & Affordable",
                "description": "We build software that small businesses and individuals can actually afford to use.",
                "icon": "💸",
                "order": 1,
            },
            {
                "title": "Community First",
                "description": "Every project is built with real users in mind — not investor dashboards.",
                "icon": "🤝",
                "order": 2,
            },
            {
                "title": "Works Everywhere",
                "description": "Our apps run on Windows, macOS, Linux, Android, and iOS — no one gets left out.",
                "icon": "🌍",
                "order": 3,
            },
            {
                "title": "Simple by Design",
                "description": "No bloat, no steep learning curves. If your staff can't figure it out in minutes, we've failed.",
                "icon": "✨",
                "order": 4,
            },
        ]

        for feature in features:
            obj, created = Feature.objects.get_or_create(
                title=feature["title"],
                defaults={
                    "description": feature["description"],
                    "icon": feature["icon"],
                    "order": feature["order"],
                    "is_active": True,
                },
            )
            self._log("Feature", feature["title"], created)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def _seed_projects(self):
        # Fetch technologies to attach to BillBasket
        tech_names = ["Flutter", "Dart", "Riverpod", "SQLite"]
        techs = list(Technology.objects.filter(name__in=tech_names))

        # Fetch team members to attach
        team = list(TeamMember.objects.filter(name="Aslam"))

        billbasket, created = Project.objects.get_or_create(
            subdomain="billbasket",
            defaults={
                "name": "BillBasket",
                "tagline": "Billing and inventory software built for supermarkets.",
                "description": (
                    "BillBasket helps store owners, managers, and cashiers run their "
                    "daily operations without the hassle. Bill customers fast, track "
                    "stock levels, manage credits, and pull sales reports — all from "
                    "one app.\n\n"
                    "Built with Flutter so it runs on Windows, macOS, Linux, Android, "
                    "and iOS. No server required — everything works offline with a "
                    "local SQLite database."
                ),
                "icon": "🛒",
                "status": "active",
                "github_url": "https://github.com/Aslamkv/billbasket",
                "order": 1,
                "is_active": True,
            },
        )

        # Attach technologies and contributors (idempotent — set() won't duplicate)
        if techs:
            billbasket.technologies.set(techs)
        if team:
            billbasket.contributors.set(team)

        self._log("Project", "BillBasket", created)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, model: str, name: str, created: bool):
        if created:
            self.stdout.write(f"  ✓ Created {model}: {name}")
        else:
            self.stdout.write(f"  · Skipped {model}: {name} (already exists)")
