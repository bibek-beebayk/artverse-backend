from django import forms


class ArtworkBulkUploadForm(forms.Form):
    csv_file = forms.FileField(
        help_text=(
            "Upload a CSV with headers like: "
            "title,slug,category,description,image_filename,is_featured,is_published,image_url"
        )
    )
    images_zip = forms.FileField(
        required=False,
        help_text="Optional ZIP file containing the artwork images referenced by image_filename.",
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        help_text="If checked, rows with an existing slug will update the matching artwork.",
    )
    auto_create_categories = forms.BooleanField(
        required=False,
        initial=True,
        help_text="If checked, missing categories from the CSV will be created automatically.",
    )
    dry_run = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Validate the CSV and ZIP without creating or updating any records.",
    )
