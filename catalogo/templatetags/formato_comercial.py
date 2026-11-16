from django import template


register = template.Library()


@register.filter
def precio_clp(precio):
    return f"${precio:,}".replace(",", ".")
