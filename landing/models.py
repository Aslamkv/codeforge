from django.db import models
from django.utils.text import slugify


class TeamMember(models.Model):
    """A contributor or community member working on Codeforge projects."""

    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100)
    bio = models.TextField(blank=True, help_text="Optional bio for the contributor")
    avatar_initials = models.CharField(
        max_length=5,
        help_text="Initials for avatar (e.g., 'JD' for Jane Developer)",
    )
    email = models.EmailField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    order = models.PositiveIntegerField(
        default=0, help_text="Order in which contributors appear"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name} — {self.role}"


class Technology(models.Model):
    """A technology used across Codeforge projects."""

    CATEGORY_CHOICES = [
        ("frontend", "Frontend"),
        ("backend", "Backend"),
        ("mobile", "Mobile"),
        ("cloud", "Cloud"),
        ("database", "Database"),
        ("devops", "DevOps"),
    ]

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "order", "name"]
        verbose_name_plural = "Technologies"

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class Feature(models.Model):
    """A value or capability that Codeforge stands for."""

    title = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=10, help_text="Emoji icon (e.g., 🤝)")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title


class SiteSettings(models.Model):
    """Site-wide settings. Only one instance should exist."""

    site_name = models.CharField(max_length=100, default="Codeforge")
    tagline = models.CharField(
        max_length=200,
        default="Built by the community, for the community.",
    )
    description = models.TextField(
        default=(
            "Codeforge is a community of developers building affordable, "
            "high-quality software for everyone. We believe technology should "
            "uplift whole communities — not just the privileged few."
        )
    )
    contact_email = models.EmailField(default="hello@codeforge.show")
    github_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return f"Settings — {self.site_name}"

    def save(self, *args, **kwargs):
        # Enforce singleton: if a record already exists and this is a new instance,
        # update the existing one instead of creating a duplicate.
        if not self.pk and SiteSettings.objects.exists():
            existing = SiteSettings.objects.first()
            for field in ["site_name", "tagline", "description", "contact_email",
                          "github_url", "twitter_url", "linkedin_url"]:
                setattr(existing, field, getattr(self, field))
            existing.save()
            return existing
        return super().save(*args, **kwargs)


class Project(models.Model):
    """A project hosted under the Codeforge umbrella."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("beta", "Beta"),
        ("planning", "Planning"),
        ("archived", "Archived"),
    ]

    name = models.CharField(max_length=100)
    subdomain = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Subdomain slug (e.g. 'billbasket' → billbasket.codeforge.show)",
    )
    tagline = models.CharField(max_length=200, help_text="One-line description shown on the landing page")
    description = models.TextField(blank=True, help_text="Longer description for the project page")
    icon = models.CharField(max_length=10, default="🛠️", help_text="Emoji icon for the project card")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planning")
    technologies = models.ManyToManyField(Technology, blank=True, related_name="projects")
    contributors = models.ManyToManyField(TeamMember, blank=True, related_name="projects")
    github_url = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0, help_text="Display order on landing page")
    is_active = models.BooleanField(default=True, help_text="Show on landing page")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name} ({self.subdomain}.codeforge.show)"

    @property
    def url(self):
        return f"https://{self.subdomain}.codeforge.show"

    def save(self, *args, **kwargs):
        # Auto-generate subdomain from name if not set
        if not self.subdomain:
            self.subdomain = slugify(self.name)
        super().save(*args, **kwargs)
