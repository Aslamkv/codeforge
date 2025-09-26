from django.db import models
from django.utils.text import slugify

class TeamMember(models.Model):
    """Model for team members"""
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100)
    bio = models.TextField(blank=True, help_text="Optional bio for the team member")
    avatar_initials = models.CharField(max_length=5, help_text="Initials for avatar (e.g., 'JD' for John Developer)")
    email = models.EmailField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0, help_text="Order in which team members appear")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.name} - {self.role}"

class Technology(models.Model):
    """Model for technologies used"""
    CATEGORY_CHOICES = [
        ('frontend', 'Frontend'),
        ('backend', 'Backend'),
        ('mobile', 'Mobile'),
        ('cloud', 'Cloud'),
        ('database', 'Database'),
        ('devops', 'DevOps'),
    ]
    
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'order', 'name']
        verbose_name_plural = "Technologies"

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

class Feature(models.Model):
    """Model for features/services"""
    title = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=10, help_text="Emoji icon (e.g., 🚀)")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'title']

    def __str__(self):
        return self.title

class SiteSettings(models.Model):
    """Model for site-wide settings"""
    site_name = models.CharField(max_length=100, default="CodeForge")
    tagline = models.CharField(max_length=200, default="Building Tomorrow's Software Today")
    description = models.TextField(default="We are a passionate development team creating innovative software solutions that drive digital transformation and empower businesses to reach their full potential.")
    contact_email = models.EmailField(default="hello@codeforge.dev")
    github_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return f"Settings for {self.site_name}"

    def save(self, *args, **kwargs):
        # Ensure only one settings instance exists
        if not self.pk and SiteSettings.objects.exists():
            # If this is a new instance and one already exists, update the existing one
            existing = SiteSettings.objects.first()
            existing.site_name = self.site_name
            existing.tagline = self.tagline
            existing.description = self.description
            existing.contact_email = self.contact_email
            existing.github_url = self.github_url
            existing.twitter_url = self.twitter_url
            existing.linkedin_url = self.linkedin_url
            existing.save()
            return existing
        return super().save(*args, **kwargs)
