import math
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox


R = 6.0
POLYGON_SIDES = 64
DEFAULT_ZOOM = 4.0


def read_dxf(file_path: str) -> tuple[str | None, list[dict]]:
    """Read the first layer name and basic drawable entities from an ASCII DXF."""
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise OSError(f"Nie można odczytać pliku: {error}") from error

    pairs = []
    index = 0
    while index + 1 < len(lines):
        pairs.append((lines[index].strip(), lines[index + 1].strip()))
        index += 2

    layer_name = None
    entities = []
    pair_index = 0
    while pair_index < len(pairs):
        code, value = pairs[pair_index]
        if code != "0":
            pair_index += 1
            continue

        record_end = pair_index + 1
        while record_end < len(pairs) and pairs[record_end][0] != "0":
            record_end += 1
        record = pairs[pair_index + 1 : record_end]

        if value.upper() == "LAYER" and layer_name is None:
            layer_name = next((item_value for item_code, item_value in record if item_code == "2"), None)
        elif value.upper() in {"LINE", "CIRCLE", "ARC", "LWPOLYLINE"}:
            entity = parse_entity(value.upper(), record)
            if entity is not None:
                entity["layer"] = next((item_value for item_code, item_value in record if item_code == "8"), None)
                entities.append(entity)
        elif value.upper() == "POLYLINE":
            vertex_points = []
            vertex_index = record_end
            while vertex_index < len(pairs) and pairs[vertex_index][1].upper() == "VERTEX":
                vertex_end = vertex_index + 1
                while vertex_end < len(pairs) and pairs[vertex_end][0] != "0":
                    vertex_end += 1
                vertex = parse_entity("VERTEX", pairs[vertex_index + 1 : vertex_end])
                if vertex is not None:
                    vertex_points.append(vertex["point"])
                vertex_index = vertex_end
            if len(vertex_points) >= 2:
                layer = next((item_value for item_code, item_value in record if item_code == "8"), None)
                entities.append({"type": "polyline", "points": vertex_points, "layer": layer})
            pair_index = vertex_index
            continue
        pair_index = record_end

    return layer_name, entities


def parse_entity(entity_type: str, record: list[tuple[str, str]]) -> dict | None:
    def number(code: str, default: float = 0.0) -> float:
        value = next((item_value for item_code, item_value in record if item_code == code), None)
        try:
            return float(value) if value is not None else default
        except ValueError:
            return default

    if entity_type == "LINE":
        return {"type": "line", "points": [(number("10"), number("20")), (number("11"), number("21"))]}
    if entity_type == "CIRCLE":
        return {"type": "circle", "center": (number("10"), number("20")), "radius": number("40")}
    if entity_type == "ARC":
        return {
            "type": "arc",
            "center": (number("10"), number("20")),
            "radius": number("40"),
            "start": number("50"),
            "end": number("51"),
        }
    if entity_type == "LWPOLYLINE":
        points = []
        x_value = None
        for code, value in record:
            if code == "10":
                try:
                    x_value = float(value)
                except ValueError:
                    x_value = None
            elif code == "20" and x_value is not None:
                try:
                    points.append((x_value, float(value)))
                except ValueError:
                    pass
        if len(points) >= 2:
            return {"type": "polyline", "points": points}
    if entity_type == "VERTEX":
        return {"type": "vertex", "point": (number("10"), number("20"))}
    return None


def polygon_side_length(radius: float, sides: int = POLYGON_SIDES) -> float:
    """Return the side length of a regular polygon inscribed in a circle."""
    if sides < 3:
        raise ValueError("Liczba boków wielokąta musi wynosić co najmniej 3.")
    return 2.0 * radius * math.sin(math.pi / sides)


def polygonized_entities(
    entities: list[dict],
    radius: float,
    sides: int = POLYGON_SIDES,
) -> list[dict]:
    """Approximate all visible geometry with nearly equal-length straight segments."""
    target_length = polygon_side_length(radius, sides)
    segments = []

    def equal_segments(points: list[tuple[float, float]], side: float = 1.0) -> None:
        if len(points) < 2:
            return
        lengths = [
            math.hypot(second[0] - first[0], second[1] - first[1])
            for first, second in zip(points, points[1:])
        ]
        total_length = sum(lengths)
        if total_length == 0:
            return
        count = max(1, round(total_length / target_length))
        step = total_length / count
        sampled = [points[0]]
        distance_to_next = step
        segment_index = 0
        segment_start = points[0]
        segment_length = lengths[0]
        for sample_index in range(1, count):
            while distance_to_next > segment_length and segment_index < len(lengths) - 1:
                distance_to_next -= segment_length
                segment_index += 1
                segment_start = points[segment_index]
                segment_length = lengths[segment_index]
            fraction = distance_to_next / segment_length
            segment_end = points[segment_index + 1]
            sampled.append(
                (
                    segment_start[0] + (segment_end[0] - segment_start[0]) * fraction,
                    segment_start[1] + (segment_end[1] - segment_start[1]) * fraction,
                )
            )
            distance_to_next += step
        sampled.append(points[-1])
        segments.extend(
            {"type": "line", "points": [first, second], "side": side}
            for first, second in zip(sampled, sampled[1:])
        )
    for entity in entities:
        if entity["type"] in {"line", "polyline"}:
            points = entity["points"]
            is_closed = points[0] == points[-1]
            side = 1.0
            if is_closed:
                signed_area = sum(
                    first[0] * second[1] - second[0] * first[1]
                    for first, second in zip(points, points[1:])
                )
                side = -1.0 if signed_area > 0 else 1.0
            equal_segments(points, side)
        elif entity["type"] in {"circle", "arc"}:
            start = 0.0 if entity["type"] == "circle" else entity["start"]
            end = 360.0 if entity["type"] == "circle" else entity["end"]
            if end <= start:
                end += 360.0
            arc_length = math.radians(end - start) * entity["radius"]
            count = max(3, round(arc_length / target_length))
            points = []
            for index in range(count + 1):
                angle = math.radians(start + (end - start) * index / count)
                points.append(
                    (
                        entity["center"][0] + math.cos(angle) * entity["radius"],
                        entity["center"][1] + math.sin(angle) * entity["radius"],
                    )
                )
            equal_segments(points, 1.0)
    return segments


def tangent_circle_centers(
    segments: list[dict],
    radius: float,
) -> list[tuple[float, float]]:
    """Return one outward circle center tangent to each simplified segment."""
    centers = []
    for segment in segments:
        first, second = segment["points"]
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        middle = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
        normal = (-dy / length * radius, dx / length * radius)
        side = segment.get("side", 1.0)
        centers.append((middle[0] + normal[0] * side, middle[1] + normal[1] * side))
    return centers


def _point_inside_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    inside = False
    point_x, point_y = point
    for first, second in zip(polygon, polygon[1:]):
        crosses_y = (first[1] > point_y) != (second[1] > point_y)
        if crosses_y:
            crossing_x = (second[0] - first[0]) * (point_y - first[1]) / (second[1] - first[1]) + first[0]
            if point_x < crossing_x:
                inside = not inside
    return inside


def _closest_point_on_segment(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, float]:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return first
    projection = max(0.0, min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length_squared))
    return first[0] + projection * dx, first[1] + projection * dy


def adjust_circle_centers(
    centers: list[tuple[float, float]],
    original_entities: list[dict],
    radius: float,
) -> list[tuple[float, float]]:
    """Move colliding centers toward a collision-free position, up to ten times."""
    closed_polygons = [
        entity["points"]
        for entity in original_entities
        if entity["type"] in {"line", "polyline"}
        and len(entity["points"]) >= 4
        and entity["points"][0] == entity["points"][-1]
    ]
    source_segments = []
    source_curves = []
    for entity in original_entities:
        if entity["type"] in {"line", "polyline"}:
            source_segments.extend(zip(entity["points"], entity["points"][1:]))
        elif entity["type"] in {"circle", "arc"}:
            source_curves.append(entity)

    cell_size = max(radius * 4.0, 1.0)
    segment_index: dict[tuple[int, int], list[tuple[tuple[float, float], tuple[float, float]]] ] = {}
    for first, second in source_segments:
        min_cell_x = math.floor(min(first[0], second[0]) / cell_size)
        max_cell_x = math.floor(max(first[0], second[0]) / cell_size)
        min_cell_y = math.floor(min(first[1], second[1]) / cell_size)
        max_cell_y = math.floor(max(first[1], second[1]) / cell_size)
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                segment_index.setdefault((cell_x, cell_y), []).append((first, second))

    def contour_data(center: tuple[float, float]):
        center_cell = (math.floor(center[0] / cell_size), math.floor(center[1] / cell_size))
        nearby_segments = []
        for offset_x in range(-1, 2):
            for offset_y in range(-1, 2):
                nearby_segments.extend(segment_index.get((center_cell[0] + offset_x, center_cell[1] + offset_y), []))
        segments_to_check = nearby_segments or source_segments
        nearest_point = None
        nearest_distance = float("inf")
        for first, second in segments_to_check:
            candidate = _closest_point_on_segment(center, first, second)
            distance = math.hypot(center[0] - candidate[0], center[1] - candidate[1])
            if distance < nearest_distance:
                nearest_point, nearest_distance = candidate, distance
        for curve in source_curves:
            dx = center[0] - curve["center"][0]
            dy = center[1] - curve["center"][1]
            distance_to_center = math.hypot(dx, dy)
            if distance_to_center == 0:
                candidate = (curve["center"][0] + curve["radius"], curve["center"][1])
            else:
                candidate = (
                    curve["center"][0] + dx / distance_to_center * curve["radius"],
                    curve["center"][1] + dy / distance_to_center * curve["radius"],
                )
            distance = abs(distance_to_center - curve["radius"])
            if distance < nearest_distance:
                nearest_point, nearest_distance = candidate, distance
        inside = any(_point_inside_polygon(center, polygon) for polygon in closed_polygons)
        return nearest_point, nearest_distance, inside

    adjusted_centers = []
    for original_center in centers:
        center = original_center
        success = False
        for _ in range(10):
            nearest_point, nearest_distance, inside = contour_data(center)
            if nearest_point is None or (nearest_distance >= radius - 1e-4 and not inside):
                success = True
                break
            direction_x = center[0] - nearest_point[0]
            direction_y = center[1] - nearest_point[1]
            direction_length = math.hypot(direction_x, direction_y)
            if direction_length == 0:
                direction_x, direction_y, direction_length = (0.0, 1.0, 1.0)
            if inside:
                direction_x, direction_y = -direction_x, -direction_y
            center = (
                nearest_point[0] + direction_x / direction_length * radius,
                nearest_point[1] + direction_y / direction_length * radius,
            )
        if success:
            adjusted_centers.append(center)
    return adjusted_centers


def draw_entities(
    canvas: tk.Canvas,
    entities: list[dict],
    circle_centers: list[tuple[float, float]] | None = None,
    circle_radius: float = 0.0,
    original_entities: list[dict] | None = None,
) -> None:
    canvas.delete("all")
    circle_centers = circle_centers or []
    original_entities = original_entities or []
    bounds = []
    for entity in [*original_entities, *entities]:
        if entity["type"] in {"line", "polyline"}:
            bounds.extend(entity["points"])
        elif entity["type"] in {"circle", "arc"}:
            x, y = entity["center"]
            radius = entity["radius"]
            bounds.extend([(x - radius, y - radius), (x + radius, y + radius)])
    for center_x, center_y in circle_centers:
        bounds.extend(
            [
                (center_x - circle_radius, center_y - circle_radius),
                (center_x + circle_radius, center_y + circle_radius),
            ]
        )
    if not bounds:
        canvas.create_text(20, 20, anchor="nw", text="Brak obsługiwanej geometrii do narysowania.", fill="#667085")
        return

    min_x = min(point[0] for point in bounds)
    max_x = max(point[0] for point in bounds)
    min_y = min(point[1] for point in bounds)
    max_y = max(point[1] for point in bounds)
    width = max(canvas.winfo_width(), 400)
    height = max(canvas.winfo_height(), 300)
    scale = min((width - 50) / max(max_x - min_x, 1), (height - 50) / max(max_y - min_y, 1))

    def point(x: float, y: float) -> tuple[float, float]:
        return 25 + (x - min_x) * scale, height - 25 - (y - min_y) * scale

    for entity in original_entities:
        if entity["type"] in {"line", "polyline"}:
            coords = [coordinate for item in entity["points"] for coordinate in point(*item)]
            canvas.create_line(*coords, fill="#16a34a", width=3, tags=("geometry", "original"))
        else:
            center_x, center_y = point(*entity["center"])
            radius = entity["radius"] * scale
            box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
            if entity["type"] == "circle":
                canvas.create_oval(*box, outline="#16a34a", width=3, tags=("geometry", "original"))
            else:
                extent = -(entity["end"] - entity["start"])
                canvas.create_arc(
                    *box,
                    start=-entity["start"],
                    extent=extent,
                    style="arc",
                    outline="#16a34a",
                    width=3,
                    tags=("geometry", "original"),
                )

    for entity in entities:
        if entity["type"] in {"line", "polyline"}:
            coords = [coordinate for item in entity["points"] for coordinate in point(*item)]
            canvas.create_line(*coords, fill="#1677b8", width=1, tags=("geometry", "simplified"))
        else:
            center_x, center_y = point(*entity["center"])
            radius = entity["radius"] * scale
            box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
            if entity["type"] == "circle":
                canvas.create_oval(*box, outline="#1677b8", width=2, tags=("geometry", "simplified"))
            else:
                extent = -(entity["end"] - entity["start"])
                canvas.create_arc(
                    *box,
                    start=-entity["start"],
                    extent=extent,
                    style="arc",
                    outline="#1677b8",
                    width=2,
                    tags=("geometry", "simplified"),
                )

    for center_x, center_y in circle_centers:
        screen_x, screen_y = point(center_x, center_y)
        screen_radius = circle_radius * scale
        canvas.create_arc(
            screen_x - screen_radius,
            screen_y - screen_radius,
            screen_x + screen_radius,
            screen_y + screen_radius,
            outline="#e58f24",
            width=1,
            dash=(4, 3),
            start=0,
            extent=359.99,
            style="arc",
            tags=("geometry", "fit-circle"),
        )
        point_radius = 0.01
        canvas.create_oval(
            screen_x - point_radius,
            screen_y - point_radius,
            screen_x + point_radius,
            screen_y + point_radius,
            fill="#dc2626",
            outline="#ffffff",
            tags=("geometry", "fit-center"),
        )
    canvas.tag_raise("simplified")


def main() -> None:
    root = tk.Tk()
    root.title("Podgląd DXF")
    root.geometry("900x680")
    root.minsize(600, 420)

    status = tk.StringVar(value="Otwieranie wyboru pliku...")
    tk.Label(root, textvariable=status, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 8))
    toolbar = tk.Frame(root)
    toolbar.pack(fill="x", padx=18, pady=(0, 8))
    canvas = tk.Canvas(root, background="#f8fafc", highlightthickness=1, highlightbackground="#d0d5dd")
    canvas.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    loaded_entities: list[dict] = []
    loaded_original_entities: list[dict] = []
    loaded_circle_centers: list[tuple[float, float]] = []

    def zoom_by(factor: float) -> None:
        center_x = canvas.canvasx(canvas.winfo_width() / 2)
        center_y = canvas.canvasy(canvas.winfo_height() / 2)
        canvas.scale("geometry", center_x, center_y, factor, factor)
        canvas.configure(scrollregion=canvas.bbox("all"))

    def fit_view() -> None:
        canvas.delete("all")
        if loaded_entities:
            draw_entities(canvas, loaded_entities, loaded_circle_centers, R, loaded_original_entities)

    tk.Button(toolbar, text="+", width=4, command=lambda: zoom_by(1.25)).pack(side="left")
    tk.Button(toolbar, text="-", width=4, command=lambda: zoom_by(0.8)).pack(side="left", padx=(4, 0))
    tk.Button(toolbar, text="Dopasuj", command=fit_view).pack(side="left", padx=(12, 0))
    tk.Label(toolbar, text="Zoom: kółko myszy | Pan: lewy lub środkowy przycisk + przeciąganie", fg="#667085").pack(side="left", padx=12)

    def zoom(event: tk.Event) -> str:
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        cursor_x = canvas.canvasx(event.x)
        cursor_y = canvas.canvasy(event.y)
        canvas.scale("geometry", cursor_x, cursor_y, factor, factor)
        canvas.configure(scrollregion=canvas.bbox("all"))
        return "break"

    def pan_start(event: tk.Event) -> None:
        canvas.scan_mark(event.x, event.y)

    def pan_move(event: tk.Event) -> None:
        canvas.scan_dragto(event.x, event.y, gain=1)

    canvas.bind("<MouseWheel>", zoom)
    canvas.bind("<Button-1>", pan_start)
    canvas.bind("<B1-Motion>", pan_move)
    canvas.bind("<Button-2>", pan_start)
    canvas.bind("<B2-Motion>", pan_move)

    def choose_and_load() -> None:
        file_path = filedialog.askopenfilename(
            parent=root,
            title="Wybierz plik DXF",
            filetypes=[("Pliki DXF", "*.dxf"), ("Wszystkie pliki", "*.*")],
        )
        if not file_path:
            root.destroy()
            return

        status.set("Wczytywanie pliku DXF...")

        def load_in_background() -> None:
            try:
                layer_name, all_entities = read_dxf(file_path)
                original_entities = [entity for entity in all_entities if entity.get("layer") == layer_name]
                simplified_entities = polygonized_entities(original_entities, R, POLYGON_SIDES)
                circle_centers = tangent_circle_centers(simplified_entities, R)
                circle_centers = adjust_circle_centers(circle_centers, original_entities, R)
                result = (layer_name, original_entities, simplified_entities, circle_centers)
                root.after(0, finish_loading, result, None, file_path)
            except OSError as error:
                root.after(0, finish_loading, None, error, file_path)

        threading.Thread(target=load_in_background, daemon=True).start()

    def finish_loading(
        result: tuple[str | None, list[dict], list[dict], list[tuple[float, float]]] | None,
        error: OSError | None,
        file_path: str,
    ) -> None:
        if error is not None:
            messagebox.showerror("Błąd odczytu", str(error), parent=root)
            root.destroy()
            return

        layer_name, original_entities, entities, circle_centers = result
        loaded_entities.clear()
        loaded_entities.extend(entities)
        loaded_original_entities.clear()
        loaded_original_entities.extend(original_entities)
        loaded_circle_centers.clear()
        loaded_circle_centers.extend(circle_centers)
        status.set(
            f"Pierwsza warstwa: {layer_name or 'nie znaleziono'} | "
            f"Oryginalnych obiektów: {len(original_entities)} | Odcinków: {len(entities)} | Kół: {len(circle_centers)} | "
            f"R = {R:g} | Bok wielokąta: {polygon_side_length(R):.2f}"
        )
        root.title(f"Podgląd pierwszej warstwy - {Path(file_path).name}")
        canvas.delete("all")

        def draw_initial_view() -> None:
            draw_entities(canvas, entities, circle_centers, R, original_entities)
            zoom_by(DEFAULT_ZOOM)

        root.after(100, draw_initial_view)

    root.after(100, choose_and_load)
    root.mainloop()


if __name__ == "__main__":
    main()