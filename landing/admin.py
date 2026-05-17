from django.contrib import admin
from .models import TeamMember, Technology, Feature, SiteSettings, Project, ContactMessage


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ['name', 'role', 'order', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'role']
    list_editable = ['order', 'is_active']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'role', 'bio', 'avatar_initials')
        }),
        ('Contact Information', {
            'fields': ('email', 'linkedin_url', 'github_url', 'twitter_url'),
            'classes': ('collapse',)
        }),
        ('Display Settings', {
            'fields': ('order', 'is_active')
        }),
    )


@admin.register(Technology)
class TechnologyAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'order', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['name', 'description']
    list_editable = ['order', 'is_active']
    fieldsets = (
        ('Technology Information', {
            'fields': ('name', 'category', 'description')
        }),
        ('Display Settings', {
            'fields': ('order', 'is_active')
        }),
    )


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ['title', 'order', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['title', 'description']
    list_editable = ['order', 'is_active']
    fieldsets = (
        ('Feature Information', {
            'fields': ('title', 'description', 'icon')
        }),
        ('Display Settings', {
            'fields': ('order', 'is_active')
        }),
    )


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Site Information', {
            'fields': ('site_name', 'tagline', 'description')
        }),
        ('Contact Information', {
            'fields': ('contact_email', 'github_url', 'twitter_url', 'linkedin_url')
        }),
    )

    def has_add_permission(self, request):
        # Only allow one settings instance
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Don't allow deletion of settings
        return False


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'subdomain', 'status', 'order', 'is_active', 'created_at']
    list_filter = ['status', 'is_active']
    search_fields = ['name', 'subdomain', 'tagline']
    list_editable = ['order', 'is_active', 'status']
    prepopulated_fields = {'subdomain': ('name',)}
    filter_horizontal = ['technologies', 'contributors']
    fieldsets = (
        ('Project Information', {
            'fields': ('name', 'subdomain', 'icon', 'tagline', 'description', 'status')
        }),
        ('Links', {
            'fields': ('github_url',),
            'classes': ('collapse',)
        }),
        ('Associations', {
            'fields': ('technologies', 'contributors'),
            'classes': ('collapse',)
        }),
        ('Display Settings', {
            'fields': ('order', 'is_active')
        }),
    )


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'subject', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'email', 'subject', 'message']
    list_editable = ['status']
    readonly_fields = ['name', 'email', 'subject', 'message', 'ip_address', 'created_at', 'updated_at']
    fieldsets = (
        ('Sender', {
            'fields': ('name', 'email', 'ip_address')
        }),
        ('Message', {
            'fields': ('subject', 'message')
        }),
        ('Status', {
            'fields': ('status', 'created_at', 'updated_at')
        }),
    )

    def has_add_permission(self, request):
        # Messages come in via the public form only
        return False
