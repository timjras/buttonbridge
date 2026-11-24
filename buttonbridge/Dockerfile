ARG BUILD_FROM
FROM ${BUILD_FROM}

# Pillow's text drawing needs FreeType; the wheels bundle it, so only the cache folder is set up here.
ENV STREAMDECK_HA_CACHE=/opt/streamdeck-ha-cache \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt

COPY renderer /app/renderer
# Fetch the icon font and the clock fonts once, at build time, so the app needs no internet to run.
RUN python3 -c "from renderer import draw, images; draw._mdi_codepoints(); draw._mdi_font(40); [images.font_path(f) for f in images.CLOCK_FONTS]"

COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
