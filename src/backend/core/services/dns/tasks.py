"""DNS tasks."""

# pylint: disable=unused-argument, broad-exception-raised, broad-exception-caught, too-many-lines

# @celery_app.task(bind=True)
# def check_maildomain_dns(self, maildomain_id):
#     """Check if the DNS records for a mail domain are correct."""

#     maildomain = models.MailDomain.objects.get(id=maildomain_id)
#     expected_records = maildomain.get_expected_dns_records()
#     for record in expected_records:
#         res = dns.resolver.resolve(
#             record["target"], record["type"], raise_on_no_answer=False, lifetime=10
#         )
#         print(res)
#         print(record)
#     return {"success": True}
