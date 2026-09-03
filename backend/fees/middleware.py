"""
Tenant resolution.

Today there is one school and this reads it from the user's profile or the
single active row. When you go multi-tenant, resolve from subdomain or a
membership table and set the Postgres session variable so the RLS policies
in sql/rls.sql become the real enforcement boundary.
"""

from django.db import connection

from fees.models import School


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        school = None
        if request.user.is_authenticated:
            school = School.objects.filter(is_active=True).first()

        request.school = school
        request.school_id = school.id if school else None

        if school and connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.school_id', %s, true)",
                            [str(school.id)])

        return self.get_response(request)
