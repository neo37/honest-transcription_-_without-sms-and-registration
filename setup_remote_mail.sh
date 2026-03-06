#!/bin/bash
set -e

DOMAIN="mail2.business-pad.com"
EMAIL="admin@business-pad.com"

# Create directories
mkdir -p /opt/mail2/data/ssl
mkdir -p /opt/mail2/data

# Nginx config for HTTP (to get Certbot working and later proxy to Poste.io)
cat << 'EOF' > /etc/nginx/sites-available/mail2.business-pad.com.conf
server {
    listen 80;
    server_name mail2.business-pad.com;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/mail2.business-pad.com.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Run Certbot to get the certificate (Nginx plugin will also configure 443 for webUI)
certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $EMAIL --redirect

# Copy certificates for Poste.io
cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem /opt/mail2/data/ssl/server.crt
cp /etc/letsencrypt/live/$DOMAIN/privkey.pem /opt/mail2/data/ssl/server.key

# Write Poste.io Docker Compose
cat << 'EOF' > /opt/mail2/docker-compose.yml
version: '3.7'
services:
  mailserver:
    image: analogic/poste.io
    container_name: mailserver
    hostname: mail2.business-pad.com
    ports:
      - "25:25"
      - "110:110"
      - "143:143"
      - "587:587"
      - "993:993"
      - "995:995"
      - "4190:4190"
      - "127.0.0.1:8080:80"
    environment:
      - TZ=Europe/Moscow
      - HTTPS=OFF
    volumes:
      - /opt/mail2/data:/data
    restart: always
EOF

# Ensure docker-compose is installed
if ! docker compose version &>/dev/null; then
    apt-get update && apt-get install -y docker-compose-plugin docker-compose || true
fi

# Stop previous if any
cd /opt/mail2
docker compose down || docker-compose down || true


# Hook to restart Poste.io whenever Certbot renews
cat << 'EOF' > /etc/letsencrypt/renewal-hooks/deploy/poste-io-cert-copy.sh
#!/bin/bash
if [ "$RENEWED_DOMAINS" = "mail2.business-pad.com" ]; then
    cp /etc/letsencrypt/live/mail2.business-pad.com/fullchain.pem /opt/mail2/data/ssl/server.crt
    cp /etc/letsencrypt/live/mail2.business-pad.com/privkey.pem /opt/mail2/data/ssl/server.key
    cd /opt/mail2 && (docker compose restart mailserver || docker-compose restart mailserver)
fi
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/poste-io-cert-copy.sh

# Start the mail server
docker compose up -d || docker-compose up -d

echo "*** Poste.io Setup Completed successfully! ***"
