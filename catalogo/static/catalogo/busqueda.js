const formularioBusqueda = document.getElementById("buscador-publico");
const campoBusqueda = document.getElementById("consulta");
const listaSugerencias = document.getElementById("sugerencias");
const estadoSugerencias = document.getElementById("estado-sugerencias");
let esperaBusqueda;
let peticionBusqueda;
let ultimaConsulta = "";

function ocultarSugerencias() {
  listaSugerencias.hidden = true;
  estadoSugerencias.textContent = "";
}

campoBusqueda.addEventListener("input", () => {
  clearTimeout(esperaBusqueda);
  if (peticionBusqueda) peticionBusqueda.abort();
  const consulta = campoBusqueda.value.trim();
  ocultarSugerencias();
  if (!consulta) {
    ultimaConsulta = "";
    listaSugerencias.replaceChildren();
    return;
  }
  if (consulta === ultimaConsulta) {
    listaSugerencias.hidden = listaSugerencias.children.length === 0;
    return;
  }
  esperaBusqueda = setTimeout(async () => {
    const control = new AbortController();
    peticionBusqueda = control;
    try {
      const direccion = new URL(formularioBusqueda.dataset.sugerencias, location.origin);
      direccion.searchParams.set("consulta", consulta);
      const respuesta = await fetch(direccion, { signal: control.signal });
      if (!respuesta.ok) throw new Error("Sugerencias no disponibles");
      const datos = await respuesta.json();
      if (control.signal.aborted || campoBusqueda.value.trim() !== consulta) return;
      listaSugerencias.replaceChildren();
      for (const producto of datos.productos) {
        const elemento = document.createElement("li");
        const boton = document.createElement("button");
        boton.type = "button";
        boton.className = "list-group-item list-group-item-action w-100 text-start";
        const nombre = document.createElement("span");
        nombre.className = "d-block fw-semibold";
        nombre.textContent = producto.nombre_producto;
        const marca = document.createElement("span");
        marca.className = "d-block small";
        marca.textContent = producto.marca;
        boton.append(nombre, marca);
        boton.addEventListener("click", () => {
          campoBusqueda.value = producto.nombre_producto;
          formularioBusqueda.requestSubmit();
        });
        elemento.append(boton);
        listaSugerencias.append(elemento);
      }
      ultimaConsulta = consulta;
      listaSugerencias.hidden = datos.productos.length === 0;
      estadoSugerencias.textContent = datos.productos.length
        ? `${datos.productos.length} sugerencias. Usa Tab o flecha abajo para seleccionarlas.`
        : "";
    } catch (error) {
      if (error.name !== "AbortError") ocultarSugerencias();
    }
  }, 250);
});

formularioBusqueda.addEventListener("keydown", (evento) => {
  const botones = [...listaSugerencias.querySelectorAll("button")];
  if (evento.key === "Escape") {
    clearTimeout(esperaBusqueda);
    if (peticionBusqueda) peticionBusqueda.abort();
    ocultarSugerencias();
    campoBusqueda.focus();
  } else if (!listaSugerencias.hidden && ["ArrowDown", "ArrowUp"].includes(evento.key)) {
    evento.preventDefault();
    const indice = botones.indexOf(document.activeElement);
    const siguiente = evento.key === "ArrowDown" ? indice + 1 : indice - 1;
    if (siguiente < 0) campoBusqueda.focus();
    else botones[Math.min(siguiente, botones.length - 1)]?.focus();
  }
});

formularioBusqueda.addEventListener("submit", () => {
  clearTimeout(esperaBusqueda);
  if (peticionBusqueda) peticionBusqueda.abort();
});

document.addEventListener("click", (evento) => {
  if (!formularioBusqueda.contains(evento.target)) {
    clearTimeout(esperaBusqueda);
    if (peticionBusqueda) peticionBusqueda.abort();
    ocultarSugerencias();
  }
});
