# Self-Hosting Guide

This guide explains how to deploy Messages in production, focusing on Messages-specific configuration and architecture.

## Overview

Messages is designed to be self-hosted. See [Architecture](./architecture.md) for component details.

## Prerequisites

- **Domain name(s)** for your email service
- **SSL certificates** for your domains
- **Server resources**: Minimum 4GB RAM

## Deployment Options

Messages supports multiple deployment strategies depending on your infrastructure and expertise level:

### Docker Compose (Recommended for most users)

**Best for**: Small to medium deployments, single-server setups, quick prototyping

**Requirements**:
- Docker and Docker Compose installed
- Single server or VM with sufficient resources
- Basic Docker knowledge

**Process**:
1. Start from the `compose.yaml` in the repository
2. Create production environment files (`env.d/production/*.defaults`)
3. Deploy to any environment where Docker Compose runs
4. Configure DNS and SSL certificates

**Advantages**:
- Simplest setup and maintenance
- Easy to understand and modify
- Quick deployment and updates
- Good for development and testing

### Ansible Deployment

**Best for**: Multi-server deployments, infrastructure automation, production environments

**Requirements**:
- Ansible knowledge
- Target servers with Docker support
- Infrastructure automation experience

**Process**:
1. Use our [ST Ansible repository](https://github.com/suitenumerique/st-ansible) as a base
2. Customize playbooks for your infrastructure
3. Deploy across multiple servers with automation
4. Configure monitoring and backup strategies

**Advantages**:
- Infrastructure as code
- Automated deployment and updates
- Multi-server support
- Production-ready with monitoring

### Kubernetes Deployment

**Best for**: Large-scale deployments, cloud-native environments, enterprise setups

**Requirements**:
- Kubernetes cluster
- Helm knowledge (when charts become available)
- Container orchestration experience

**Process**:
1. Wait for Helm charts (coming in future releases)
2. Deploy to Kubernetes cluster
3. Configure ingress controllers and load balancers
4. Set up monitoring with Prometheus/Grafana

**Advantages**:
- High availability and scalability
- Advanced orchestration features
- Cloud-native deployment patterns
- Enterprise-grade monitoring and logging

**Note**: Kubernetes deployment might be supported in future releases with official Helm charts.

## Messages-Specific Configuration

### 1. Technical Domain Setup

Messages uses a technical domain concept for DNS infrastructure:

```bash
# Set your technical domain
MESSAGES_TECHNICAL_DOMAIN=mail.yourdomain.com
```

**Technical Domain DNS Records:**
```
mx1.mail.yourdomain.com. A YOUR_SERVER_IP
mx2.mail.yourdomain.com. A YOUR_SERVER_IP
_spf.mail.yourdomain.com. TXT "v=spf1 ip4:YOUR_SERVER_IP -all"
```

**Customer Domain DNS Records:**
```
@ MX 10 mx1.customer-domain.com.
@ MX 20 mx2.customer-domain.com.
@ TXT "v=spf1 include:_spf.mail.yourdomain.com -all"
_dmarc TXT "v=DMARC1; p=reject; adkim=s; aspf=s;"
```

The DNS records for each customer domains are available either via API at http://localhost:8901/api/v1.0/maildomains/{maildomain-uuid}/ or in the admin interface at http://localhost:8900/domains

### 2. Environment Configuration

Messages uses environment variables as the primary configuration method:

**Environment File Structure:**
- `env.d/production/backend.defaults` - Main Django application settings
- `env.d/production/frontend.defaults` - Frontend configuration
- `env.d/production/mta-in.defaults` - Inbound mail server settings
- `env.d/production/mta-out.defaults` - Outbound mail server settings
- `env.d/production/postgresql.defaults` - Database configuration
- `env.d/production/keycloak.defaults` - Identity provider settings

**For detailed environment variable documentation, see [Environment Variables](./env.md).**

### 3. MTA Configuration

#### MTA-in (Inbound Email)
- Configured via `env.d/production/mta-in.defaults`
- Uses custom milter for synchronous delivery during SMTP sessions
- Validates recipients via REST API before accepting messages

#### MTA-out (Outbound Email)
- Configured via `env.d/production/mta-out.defaults`
- Supports relay configuration for external SMTP providers
- Requires TLS certificates for production

### 4. DNS Management

Messages includes automated DNS management:

```bash
# Check DNS records for a customer domain
python manage.py dns_check --domain example.com

# Provision DNS records automatically
python manage.py dns_provision --domain example.com --provider scaleway

# Simulate provisioning without making changes
python manage.py dns_provision --domain example.com --pretend
```

**Supported DNS Providers:**
- Scaleway DNS (full automation support)

### 5. Domain and Mailbox Management

#### Creating Mail Domains
```bash
# Via Django admin at /admin/
# Via API endpoints
# Via management commands
python manage.py shell
>>> from core.models import MailDomain
>>> MailDomain.objects.create(name='customer-domain.com')
```

#### Mailbox Creation
- Manual creation through admin interface
- Automatic creation via OIDC integration
- Programmatic creation via API

### 6. Identity Management

Messages uses OpenID Connect (OIDC) for user authentication. This is the only authentication method supported.

**OIDC Configuration Options:**

1. **Bundled Keycloak** (Recommended for most deployments)
   - Keycloak is included in the default Docker Compose setup
   - Pre-configured with Messages realm and users
   - Suitable for organizations wanting a self-hosted identity provider
   - Configure via `env.d/production/keycloak.defaults`

2. **External OIDC Provider**
   - Use any OIDC-compliant identity provider
   - Examples: Auth0, Okta, Azure AD, Google Workspace
   - Configure via `env.d/production/backend.defaults`
   - Requires proper OIDC endpoint configuration

**User Management:**
- Users are created automatically when they first log in via OIDC
- Mailboxes can be created automatically based on OIDC email addresses

### 7. Production Deployment

For production deployment, create your own Docker Compose configuration based on `compose.yaml`:

**Key Considerations:**
- Use production environment files (`env.d/production/*.defaults`)
- Configure SSL/TLS certificates
- Set up persistent volumes for databases
- Implement proper restart policies
- Configure reverse proxy (nginx) for SSL termination

## Security Considerations

### Messages-Specific Security
- **MDA API Secret**: Use strong, unique `MDA_API_SECRET`
- **OIDC Configuration**: Properly configure Keycloak endpoints
- **Technical Domain**: Secure DNS records for your technical domain
- **Environment Files**: Never commit production secrets

### IP Reputation Management

**Monitoring:**
- Check your server's IP reputation at [MXToolbox](https://mxtoolbox.com/blacklists.aspx)
- Monitor key blacklists: Spamhaus, Barracuda, SORBS

**Recovery from Blacklisting:**
1. Stop all outgoing email immediately
2. Check server logs for abuse indicators
3. Follow blacklist's delisting procedure
4. Implement stricter authentication and rate limiting

## Troubleshooting

### Common Messages Issues

1. **MTA-in not receiving emails**
   - Check firewall settings for port 25
   - Verify DNS MX records point to your technical domain
   - Check MTA-in logs for API connection issues

2. **MTA-out not sending emails**
   - Verify SMTP credentials in environment files
   - Check relay host configuration
   - Review MTA-out logs for authentication errors

3. **DNS issues**
   - Use `dns_check` command to verify records
   - Ensure technical domain A records are correct
   - Check DNS propagation with `dig`

4. **Authentication problems**
   - Verify Keycloak configuration in environment files
   - Check OIDC endpoint URLs
   - Review backend logs for authentication errors

## Next Steps

After setting up your production environment:

1. **Test thoroughly** with a small group of users
2. **Monitor performance** and adjust resources as needed
3. **Set up automated backups** and monitoring
4. **Plan for scaling** as your user base grows

For additional help, join the [Matrix community](https://matrix.to/#/#messages-official:matrix.org)!
