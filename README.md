# Artverse Backend

This Django backend is designed to replace the current Firebase-first flows in the Artverse frontend with a traditional API backend.

## Recommended stack

- Django
- Django REST Framework
- PostgreSQL in production
- Simple JWT for API auth
- Django admin for content management

## Initial domain coverage

- `accounts`: users, profiles, auth
- `gallery`: categories, artworks, videos, favorites
- `shop`: product catalog, notification signups
- `generator`: generation requests and generated images

## Local setup

1. Create a virtual environment.
2. Install requirements:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and adjust values.
4. Run migrations:
   `python manage.py makemigrations`
   `python manage.py migrate`
5. Create an admin user:
   `python manage.py createsuperuser`
6. Start the server:
   `python manage.py runserver`

## Frontend migration path

1. Replace Firebase auth with JWT login and `GET /api/auth/me/`.
2. Replace `constants.ts` data with API calls:
   - `/api/gallery/categories/`
   - `/api/gallery/artworks/`
   - `/api/gallery/videos/`
   - `/api/shop/products/`
3. Replace favorites with:
   - `GET /api/gallery/favorites/`
   - `POST /api/gallery/favorites/toggle/`
4. Move image generation behind:
   - `POST /api/generator/requests/`
   - `GET /api/generator/images/`

## Notes

- The image generation API is scaffolded as a queue-style request endpoint.
- AI provider integration should be added server-side later to keep API keys private.
- The backend defaults to SQLite locally when PostgreSQL env vars are unset.
