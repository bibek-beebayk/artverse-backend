from rest_framework import serializers

from .models import GeneratedImage, GenerationRequest


class GenerationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationRequest
        fields = (
            "id",
            "prompt",
            "style",
            "provider",
            "model_name",
            "status",
            "error_message",
            "created_at",
        )
        read_only_fields = ("id", "status", "error_message", "created_at")


class GeneratedImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedImage
        fields = ("id", "prompt", "image", "image_url", "created_at")

    def get_image(self, obj: GeneratedImage):
        if not obj.image:
            return None
        try:
            return obj.image.url
        except Exception:
            return None
