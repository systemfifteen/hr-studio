FROM nginx:alpine

COPY index.html /usr/share/nginx/html/index.html
COPY dashboard.js /usr/share/nginx/html/dashboard.js

# Jednoduchý nginx config — proxy /api na backend
COPY nginx.conf /etc/nginx/conf.d/default.conf
