from django.urls import path
from . import views

urlpatterns = [
    # Authentication endpoints
    path('auth/login/', views.login_with_passcode, name='login'),
    path('auth/reset-passcode/', views.reset_passcode, name='reset_passcode'),
    path('auth/status/', views.check_auth_status, name='auth_status'),
    path('auth/logout/', views.logout, name='logout'),
    
    # PDF endpoints (protected)
    path('upload/', views.upload_pdf, name='upload_pdf'),
    path('list/', views.list_pdf_uploads, name='list_pdf_uploads'),
    path('results/<int:pdf_id>/', views.get_pdf_results, name='get_pdf_results'),
    path('stop/<int:pdf_id>/', views.stop_pdf_processing, name='stop_pdf_processing'),
    path('delete/<int:pdf_id>/', views.delete_pdf_upload, name='delete_pdf_upload'),
] 