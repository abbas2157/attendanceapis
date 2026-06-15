from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView
from attendance.views_auth import CustomTokenObtainPairView  # ← changed

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('attendance.urls')),

    # Auth
    path('api/auth/login/',   CustomTokenObtainPairView.as_view(),  name='token_obtain_pair'),  # ← changed
    path('api/auth/refresh/', TokenRefreshView.as_view(),           name='token_refresh'),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)