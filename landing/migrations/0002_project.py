from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('subdomain', models.SlugField(max_length=100, unique=True, help_text="Subdomain slug (e.g. 'billbasket' → billbasket.codeforge.show)")),
                ('tagline', models.CharField(max_length=200, help_text='One-line description shown on the landing page')),
                ('description', models.TextField(blank=True, help_text='Longer description for the project page')),
                ('icon', models.CharField(default='🛠️', max_length=10, help_text='Emoji icon for the project card')),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('beta', 'Beta'), ('planning', 'Planning'), ('archived', 'Archived')],
                    default='planning',
                    max_length=20,
                )),
                ('github_url', models.URLField(blank=True)),
                ('order', models.PositiveIntegerField(default=0, help_text='Display order on landing page')),
                ('is_active', models.BooleanField(default=True, help_text='Show on landing page')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('technologies', models.ManyToManyField(blank=True, related_name='projects', to='landing.technology')),
                ('contributors', models.ManyToManyField(blank=True, related_name='projects', to='landing.teammember')),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
    ]
