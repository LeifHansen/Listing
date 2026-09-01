# Thryft Shop marketing site — nginx serving the built static site.
#
# There is deliberately no build stage here. The site imports its brand tokens
# from frontend/src/styles/tokens.css, which lives OUTSIDE this directory, so
# a Docker build context of marketing/ cannot see it — and a context of the
# repo root would be filtered by the root .dockerignore, which excludes
# marketing/ to keep it out of the *app's* build context.
#
# So `npm run build` runs before the image does, on a checkout that has the
# whole repo. That is also what CI already does, and what the link/SEO checks
# already ran against — meaning this image ships exactly the bytes that were
# verified, rather than a second build that could differ.
#
# Build it with: (cd marketing && npm ci && npm run build && fly deploy)
FROM nginx:1.27-alpine

RUN mkdir -p /etc/nginx/snippets
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY dist /usr/share/nginx/html

# Two ways this image can be wrong in a way nothing else would notice: built
# with no dist at all, or built from a run where the tokens.css import broke
# and the site rendered unstyled. Both produce a working nginx serving
# something useless, so check here rather than find out in a browser.
RUN test -f /usr/share/nginx/html/index.html \
      || { echo "no index.html — run 'npm run build' before building this image"; exit 1; }; \
    css=$(find /usr/share/nginx/html/_astro -name '*.css' | head -1); \
    test -n "$css" \
      || { echo "no stylesheet in the build"; exit 1; }; \
    for t in --brand-blue --bg --radius-card --shadow-card; do \
      grep -q -- "$t" "$css" \
        || { echo "$t missing from the CSS — the frontend/src/styles/tokens.css import is broken"; exit 1; }; \
    done; \
    echo "OK: index.html present and brand tokens reached the stylesheet"

# Which commit this image was built from. The app's Dockerfile carries the
# same stamp for the same reason: a deploy can report success while the old
# image keeps serving, and without this there is no way to ask the running
# site what it is. The deploy workflow polls /.build until it reports the
# commit it just shipped.
ARG GIT_SHA=""
RUN printf '%s\n' "$GIT_SHA" > /usr/share/nginx/html/.build

EXPOSE 8080
