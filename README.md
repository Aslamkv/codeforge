# CodeForge - Development Team Landing Page

A modern, responsive landing page for a development team built with Django.

## Features

- **Modern Design**: Clean, professional UI with smooth animations
- **Responsive Layout**: Works perfectly on desktop, tablet, and mobile devices
- **Interactive Elements**: Smooth scrolling, hover effects, and scroll animations
- **Dynamic Content**: Database-driven content management through Django admin
- **Team Showcase**: Display team members and their roles (customizable)
- **Technology Stack**: Highlight the technologies and tools used (customizable)
- **Features Section**: Showcase your services and capabilities (customizable)
- **Contact Information**: Easy ways to get in touch (customizable)
- **Admin Interface**: Full Django admin interface for content management

## Technologies Used

- **Backend**: Django 4.2
- **Frontend**: HTML5, CSS3, JavaScript
- **Styling**: Custom CSS with CSS Grid and Flexbox
- **Fonts**: Inter font family from Google Fonts
- **Icons**: Emoji icons for a modern touch

## Getting Started

### Prerequisites

- Python 3.9+
- pip
- virtualenv (recommended)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd codeforge
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
Create a `.env` file in the project root with:
```
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

5. Run migrations:
```bash
python manage.py migrate
```

6. Create a superuser for admin access:
```bash
python manage.py createsuperuser
```

7. Start the development server:
```bash
python manage.py runserver
```

8. Open your browser and visit:
   - Landing page: `http://localhost:8000`
   - Admin interface: `http://localhost:8000/admin`

## Project Structure

```
codeforge/
├── codeforge/          # Django project settings
├── landing/            # Landing page app
│   ├── models.py       # Database models
│   ├── admin.py        # Admin interface
│   ├── views.py        # View functions
│   ├── urls.py         # URL patterns
│   ├── apps.py         # App configuration
│   └── __init__.py     # Python package marker
├── templates/          # HTML templates
│   └── landing/
│       └── home.html   # Main landing page template
├── static/             # Static files
│   └── landing/
│       └── css/
│           └── style.css # Main stylesheet
├── manage.py           # Django management script
└── requirements.txt    # Python dependencies
```

## Admin Interface

The landing page includes a full Django admin interface for easy content management:

### Accessing the Admin
1. Go to `http://localhost:8000/admin`
2. Login with your superuser credentials
3. Manage all content through the admin interface

### Available Models

#### Site Settings
- **Site Name**: Your team/company name
- **Tagline**: Main headline for the landing page
- **Description**: Brief description of your team
- **Contact Information**: Email and social media links

#### Team Members
- **Name & Role**: Team member information
- **Avatar Initials**: Letters displayed in the avatar circle
- **Bio**: Optional bio text
- **Contact Links**: Email, LinkedIn, GitHub, Twitter
- **Order**: Control the display order
- **Active Status**: Show/hide team members

#### Features
- **Title & Description**: Service/feature information
- **Icon**: Emoji icon for the feature
- **Order**: Control the display order
- **Active Status**: Show/hide features

#### Technologies
- **Name & Category**: Technology information
- **Description**: Brief description
- **Order**: Control the display order
- **Active Status**: Show/hide technologies

## Customization

### Colors
The color scheme can be customized by modifying the CSS variables in `static/landing/css/style.css`:

```css
:root {
    --primary-color: #2563eb;
    --primary-dark: #1d4ed8;
    --secondary-color: #64748b;
    --accent-color: #06b6d4;
    /* ... other variables */
}
```

### Content Management
All content is now managed through the Django admin interface:
- **Team Information**: Add/edit team members, roles, and bios
- **Features**: Customize your services and capabilities
- **Technologies**: Update your tech stack
- **Site Settings**: Modify site name, tagline, and contact info

### Fallback Content
If no content is added through the admin, the landing page will display default content to ensure it always looks good.

## Deployment

For production deployment:

1. Set `DEBUG=False` in your environment variables
2. Configure proper `ALLOWED_HOSTS`
3. Set up a production database
4. Configure static file serving
5. Use a production WSGI server like Gunicorn

## License

This project is licensed under the MIT License - see the LICENSE file for details.
