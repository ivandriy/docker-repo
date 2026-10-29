FROM joomla:5-php8.3-apache

# Bitnami defaults the official image does not have
RUN { \
    echo 'upload_max_filesize = 64M'; \
    echo 'post_max_size = 64M'; \
    echo 'memory_limit = 256M'; \
    echo 'max_execution_time = 300'; \
    echo 'expose_php = Off'; \
    } > /usr/local/etc/php/conf.d/joomla-custom.ini
