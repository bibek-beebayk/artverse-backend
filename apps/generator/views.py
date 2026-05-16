from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GeneratedImage, GenerationRequest
from .serializers import GeneratedImageSerializer, GenerationRequestSerializer


class GenerationRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = GenerationRequest.objects.filter(user=request.user)
        serializer = GenerationRequestSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = GenerationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        generation_request = GenerationRequest.objects.create(
            user=request.user,
            prompt=serializer.validated_data["prompt"],
            style=serializer.validated_data.get("style", ""),
            provider=serializer.validated_data.get("provider", "gemini"),
            model_name=serializer.validated_data.get("model_name", ""),
        )
        response_serializer = GenerationRequestSerializer(generation_request)
        return Response(
            {
                "request": response_serializer.data,
                "message": "Generation request accepted. Provider integration should be added server-side next.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class GeneratedImageListView(ListAPIView):
    serializer_class = GeneratedImageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return GeneratedImage.objects.filter(user=self.request.user)
