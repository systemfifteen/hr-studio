FROM nginx:alpine
COPY mockup/hearthrate_mockup.html /usr/share/nginx/html/index.html
RUN printf 'server{\nlisten 80;\ncharset utf-8;\nlocation/{\nroot /usr/share/nginx/html;\nindex index.html;\n}\n}' > /etc/nginx/conf.d/default.conf
