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
  - mockup templates and mockup render jobs

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
   - `/api/gallery/collections/`
   - `/api/gallery/artworks/`
   - `/api/gallery/videos/`
   - `/api/shop/products/`
3. Replace favorites with:
   - `GET /api/gallery/favorites/`
   - `POST /api/gallery/favorites/toggle/`
4. Move image generation behind:
   - `POST /api/generator/requests/`
   - `GET /api/generator/images/`

## Mockup preview pipeline

The backend now includes the first scaffolding for backend-driven merch previews.

### Core models

- `MockupTemplate`
  - Stores the base product image, mask layer, lighting layers, supported colors/sizes, and JSON config for print placement and warp rules.
- `MockupRender`
  - Stores a single requested preview render for a source design plus template/variant combination.
  - Uses a deterministic `cache_key` so the same preview can be reused instead of rerendered.

### Current API endpoints

- `GET /api/generator/mockup-templates/`
  - Returns active product templates the frontend can offer.
- `GET /api/generator/mockup-renders/`
  - Lists recent render jobs, optionally filtered by `generated_image_id`, `source_image_url`, or `status`.
- `POST /api/generator/mockup-renders/`
  - Creates or reuses a mockup render job.
  - Accepts:
    - `template_id`
    - one of `generated_image_id`, `artwork_id`, or `source_image_url`
    - optional `source_prompt`
    - optional `variant_color`
    - optional `variant_size`
- `GET /api/generator/mockup-renders/<id>/`
  - Retrieves a single render job and its status.

### Recommended production flow

1. User generates or selects a design.
2. Frontend requests available templates from `/api/generator/mockup-templates/`.
3. Frontend posts one render request per product/variant to `/api/generator/mockup-renders/`.
4. Backend computes a cache key from:
   - source design fingerprint
   - template slug/version
   - color
   - size
5. If a matching render already exists, it is returned immediately.
6. If not, the backend stores a `pending` render record.
7. A background worker should later:
   - load the base product image
   - resize and warp the design into the print area
   - apply clipping masks, shadows, and highlights
   - save the final preview to storage
   - mark the render as `ready`

### Current rendering behavior

The current implementation already performs synchronous rendering in Django using Pillow when a mockup render is created.

- When the frontend sends an `artwork_id`, the backend now prefers the gallery artwork's stored image.
- If the artwork only has a remote `image_url`, the backend caches a normalized copy into `SourceDesignAsset` storage first, then renders from that local asset on future requests.
- In Django admin, `SourceDesignAsset` no longer needs a separate uploaded image when an `Artwork` is selected. The asset will inherit the artwork image automatically, and `source_url` is only a fallback/original-source reference.

- If a render does not exist yet, `POST /api/generator/mockup-renders/` will try to render it immediately.
- If a matching cached render already exists, the API returns the existing record.
- A manual reprocessor command is also available:
  - `python manage.py process_mockup_renders`
  - `python manage.py process_mockup_renders --retry-failed`
  - `python manage.py process_mockup_renders --render-id 12`
  - `python manage.py seed_mockup_templates`
  - `python manage.py cache_source_design_assets`
  - `python manage.py cache_source_design_assets --artwork-id 4`

### Template config JSON shape

Each `MockupTemplate` accepts a `config` JSON object. The current renderer uses a `placement` block:

```json
{
  "placement": {
    "x": 220,
    "y": 180,
    "width": 420,
    "height": 460,
    "fit": "contain",
    "rotation": 0,
    "opacity": 0.95,
    "corner_radius": 0
  }
}
```

Supported keys now:

- `x`
- `y`
- `width`
- `height`
- `fit`
  - `contain` or `cover`
- `rotation`
- `opacity`
- `corner_radius`

Recommended asset setup for a T-shirt template:

- `base_image`
  - the clean shirt mockup
- `mask_image`
  - grayscale full-canvas mask limiting where the design can appear
- `shadow_layer`
  - transparent PNG with folds/shadows above the design
- `highlight_layer`
  - transparent PNG with highlights above the design

### Starter templates included

The backend now includes an offline seed command that generates simple local starter assets for:

- `Starter T-Shirt Black`
- `Starter Hoodie Black`

Run:

```bash
python manage.py seed_mockup_templates
```

Starter placement values:

- T-shirt
```json
{
  "placement": {
    "x": 390,
    "y": 310,
    "width": 420,
    "height": 430,
    "fit": "contain",
    "rotation": 0,
    "opacity": 0.96,
    "corner_radius": 18
  }
}
```

- Hoodie
```json
{
  "placement": {
    "x": 360,
    "y": 340,
    "width": 480,
    "height": 420,
    "fit": "contain",
    "rotation": 0,
    "opacity": 0.95,
    "corner_radius": 20
  }
}
```

### Suggested next step

Add a real async render worker with:

- Celery + Redis for job execution
- Pillow for compositing
- OpenCV for perspective transforms
- Railway Buckets for rendered preview storage

## Bulk artwork upload

The Django admin now includes a bulk uploader for artworks.

Where to find it:

1. Open Django admin
2. Go to `Gallery > Artworks`
3. Click `Bulk Upload`

Upload inputs:

- a CSV manifest
- an optional ZIP of image files referenced by `image_filename`

Supported CSV headers:

```csv
title,slug,category,collection,description,image_filename,is_featured,is_published,image_url
```

Notes:

- `title` and `category` are required
- `collection` is optional
- `slug` is optional and will be generated from `title` if omitted
- `image_filename` should match a file inside the uploaded ZIP
- `image_url` can be used when you don't want to upload the image file
- `update_existing` lets you update rows that match an existing slug
- `auto_create_categories` lets the importer create missing categories
- `dry_run` validates the import without writing any data
- for large production uploads, keep `WEB_CONCURRENCY=1` and raise `GUNICORN_TIMEOUT` (for example `300`) so Railway workers have enough time to finish admin imports
- the importer now streams ZIP members one file at a time instead of loading the entire ZIP into memory first, which is much safer for large archives

## Thumbnails

Uploaded artwork and product images now generate smaller thumbnail files automatically for list views.

- artwork thumbnails are stored under `artworks/thumbnails/`
- product thumbnails are stored under `products/thumbnails/`
- APIs return both the full image and the thumbnail URL

To backfill thumbnails for existing uploaded media:

```bash
python manage.py generate_thumbnails
```

Use `--force` to regenerate thumbnails for everything:

```bash
python manage.py generate_thumbnails --force
```

## Notes

- The image generation API is scaffolded as a queue-style request endpoint.
- AI provider integration should be added server-side later to keep API keys private.
- The backend defaults to SQLite locally when PostgreSQL env vars are unset.
