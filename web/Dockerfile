FROM nginx:alpine

# nginxの設定ファイルをコピー
COPY nginx.conf /etc/nginx/nginx.conf

# HTMLファイルをコピー
COPY html /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
