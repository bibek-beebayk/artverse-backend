from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    """Shared pagination envelope for public list endpoints (shop products, gallery artworks).
    Adds `page`/`page_size`/`total_pages` on top of DRF's default `count`/`next`/`previous`/
    `results` so the frontend never has to re-derive page count from `count`/`page_size` itself."""

    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "page": self.page.number,
                "page_size": self.page.paginator.per_page,
                "total_pages": self.page.paginator.num_pages,
                "results": data,
            }
        )
