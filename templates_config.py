from fastapi.templating import Jinja2Templates

from csrf import get_csrf_token


templates = Jinja2Templates(
    directory="templates"
)

# Dostupné vo všetkých šablónach ako {{ csrf_token(request) }}.
templates.env.globals["csrf_token"] = get_csrf_token
