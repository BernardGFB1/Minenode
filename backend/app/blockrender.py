"""Isometric renderer for Minecraft block models.

The implementation renders a block from its JSON model + textures (the same data the vanilla
client uses for inventory icons), producing a shaded 3D image instead of a flat texture. Assets
are looked up in a list of roots ordered from lowest to highest priority, so mods and resource
packs can transparently override vanilla models/textures.

Based on the well-known Minecraft model -> 2D projection approach: 3D vertices of every model
element are rotated into an isometric camera, back-facing sides are culled, the remaining faces
are depth-sorted (painter's algorithm) and each texture is affine-warped onto its quad with a
directional shade matching Minecraft's GUI lighting.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

# Mnożniki jasności ścian (identyczne jak w oświetleniu GUI Minecrafta)
FACE_SHADING = {
    "up": 1.0,
    "north": 0.8,
    "south": 0.8,
    "east": 0.6,
    "west": 0.6,
    "down": 0.5,
}

# Kolejność wierzchołków dla każdej ściany w układzie:
# [Lewy-Górny UV, Prawy-Górny UV, Lewy-Dolny UV, Prawy-Dolny UV]
# Indeksy wierzchołków pudełka (0..7) zdefiniowane w _get_box_vertices
FACE_VERTICES = {
    "north": (1, 0, 5, 4),  # -Z (patrząc od północy: +X jest po lewej)
    "south": (3, 2, 7, 6),  # +Z (patrząc od południa: -X jest po lewej)
    "west": (0, 3, 4, 7),   # -X
    "east": (2, 1, 6, 5),   # +X
    "up": (0, 1, 3, 2),     # +Y
    "down": (7, 6, 4, 5),   # -Y
}


class MinecraftBlockRenderer:
    def __init__(self, assets_dir: str | Path | list[str | Path]):
        """*assets_dir* is a path (or ordered list of paths) containing ``<namespace>/models``
        and ``<namespace>/textures``. Later roots override earlier ones.
        """
        if isinstance(assets_dir, (str, Path)):
            assets_dir = [assets_dir]
        self.assets_dirs = [Path(p) for p in assets_dir]
        # Backwards-compatible alias for single-root callers.
        self.assets_dir = self.assets_dirs[0] if self.assets_dirs else Path(".")
        self._texture_cache: dict[str, Image.Image] = {}
        self._model_cache: dict[str, dict | None] = {}

    def _find(self, *parts: str) -> Path | None:
        """Find a file in the asset roots, highest priority (last) first."""
        rel = Path(*parts)
        for root in reversed(self.assets_dirs):
            candidate = root / rel
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _strip_namespace(res_loc: str) -> tuple[str, str]:
        if ":" in res_loc:
            ns, path = res_loc.split(":", 1)
            return ns, path
        return "minecraft", res_loc

    def has_model(self, model_loc: str) -> bool:
        ns, path = self._strip_namespace(model_loc)
        return self._find(ns, "models", f"{path}.json") is not None

    def load_model_json(self, model_loc: str) -> dict:
        ns, path = self._strip_namespace(model_loc)
        file_path = self._find(ns, "models", f"{path}.json")
        if file_path is None:
            raise FileNotFoundError(model_loc)
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def resolve_model(self, model_loc: str) -> dict:
        """Rekurencyjnie łączy hierarchię parentów, tekstury i elementy modelu."""
        chain = []
        curr = model_loc
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            data = self.load_model_json(curr)
            chain.append(data)
            curr = data.get("parent")

        # Odwracamy, aby iść od najstarszego przodka do dziecka
        chain.reverse()

        merged_textures = {}
        elements = []
        for node in chain:
            if "textures" in node:
                merged_textures.update(node["textures"])
            if "elements" in node:
                elements = node["elements"]

        # Rozwiązywanie referencji typu "#side" -> "#all" -> "minecraft:block/cobblestone"
        resolved_textures = {}
        for key, val in merged_textures.items():
            target = val
            seen = set()
            while isinstance(target, str) and target.startswith("#"):
                ref_key = target[1:]
                if ref_key in seen or ref_key not in merged_textures:
                    break
                seen.add(ref_key)
                target = merged_textures[ref_key]
            resolved_textures[key] = target

        return {"textures": resolved_textures, "elements": elements}

    @staticmethod
    def _missing_texture() -> Image.Image:
        """Graceful magenta placeholder when a mod references a missing texture."""
        img = Image.new("RGBA", (16, 16), (255, 0, 255, 255))
        ImageDraw.Draw(img).rectangle((0, 0, 15, 15), outline=(0, 0, 0, 255))
        return img

    def get_texture(self, tex_loc: str, tint_rgb: tuple[int, int, int] | None = None) -> Image.Image:
        """Wczytuje teksturę PNG (obsługuje też animowane paski biorąc 1. klatkę)."""
        cache_key = f"{tex_loc}_{tint_rgb}"
        if cache_key in self._texture_cache:
            return self._texture_cache[cache_key]

        ns, path = self._strip_namespace(tex_loc)
        file_path = self._find(ns, "textures", f"{path}.png")
        if file_path is None:
            img = self._missing_texture()
            self._texture_cache[cache_key] = img
            return img
        try:
            img = Image.open(file_path).convert("RGBA")
        except (OSError, ValueError):
            img = self._missing_texture()

        # Jeśli tekstura jest paskiem animacji (wysokość > szerokość), wytnij pierwszą klatkę
        w, h = img.size
        if h > w:
            img = img.crop((0, 0, w, w))

        # Opcjonalne kolorowanie (np. dla trawy, liści, wody)
        if tint_rgb is not None:
            r_t, g_t, b_t = tint_rgb
            r, g, b, a = img.split()
            r = r.point(lambda p: int(p * r_t / 255))
            g = g.point(lambda p: int(p * g_t / 255))
            b = b.point(lambda p: int(p * b_t / 255))
            img = Image.merge("RGBA", (r, g, b, a))

        self._texture_cache[cache_key] = img
        return img

    @staticmethod
    def _rotate_point(pt, axis: str, angle_deg: float, origin) -> tuple[float, float, float]:
        """Obraca punkt 3D wokół zadanego punktu (origin) i osi ('x', 'y', 'z')."""
        x, y, z = pt[0] - origin[0], pt[1] - origin[1], pt[2] - origin[2]
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)

        if axis == "x":
            y, z = y * c - z * s, y * s + z * c
        elif axis == "y":
            x, z = x * c + z * s, -x * s + z * c
        elif axis == "z":
            x, y = x * c - y * s, x * s + y * c

        return (x + origin[0], y + origin[1], z + origin[2])

    def _get_box_vertices(self, elem: dict) -> list[tuple[float, float, float]]:
        """Zwraca 8 wierzchołków elementu po zastosowaniu jego lokalnej rotacji."""
        x1, y1, z1 = elem["from"]
        x2, y2, z2 = elem["to"]

        # 4 górne (y2) i 4 dolne (y1) wierzchołki prostopadłościanu
        verts = [
            (x1, y2, z1),  # 0: minX, maxY, minZ
            (x2, y2, z1),  # 1: maxX, maxY, minZ
            (x2, y2, z2),  # 2: maxX, maxY, maxZ
            (x1, y2, z2),  # 3: minX, maxY, maxZ
            (x1, y1, z1),  # 4: minX, minY, minZ
            (x2, y1, z1),  # 5: maxX, minY, minZ
            (x2, y1, z2),  # 6: maxX, maxY, maxZ
            (x1, y1, z2),  # 7: minX, minY, maxZ
        ]

        # Obsługa lokalnej rotacji elementu (np. pochodnie, schody ukośne, kwiaty)
        if "rotation" in elem:
            rot = elem["rotation"]
            origin = tuple(rot["origin"])
            axis = rot["axis"]
            angle = rot["angle"]
            verts = [self._rotate_point(v, axis, angle, origin) for v in verts]

        return verts

    def _project_to_camera(
        self,
        pt: tuple[float, float, float],
        rot_y: float = 45.0,
        rot_x: float = 30.0,
        block_rot_y: float = 0.0,
        block_rot_x: float = 0.0,
    ) -> tuple[float, float, float]:
        """Przekształca punkt ze świata [0..16] do przestrzeni kamery (screen_x, screen_y, depth)."""
        center = (8.0, 8.0, 8.0)

        # 1. Rotacja samego bloku z blockstate (np. obrót schodów w stronę north/east/south/west)
        if block_rot_x:
            pt = self._rotate_point(pt, "x", -block_rot_x, center)
        if block_rot_y:
            pt = self._rotate_point(pt, "y", -block_rot_y, center)

        # 2. Rotacja kamery izometrycznej
        pt = self._rotate_point(pt, "y", rot_y, center)
        pt = self._rotate_point(pt, "x", rot_x, center)

        # W przestrzeni kamery: +X w prawo, +Y w górę (na obrazie w dół, więc -Y), +Z w stronę widza
        return (pt[0] - 8.0, -(pt[1] - 8.0), pt[2] - 8.0)

    @staticmethod
    def _solve_affine(
        src_pts: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
        dst_pts: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    ) -> tuple[float, float, float, float, float, float]:
        """
        Oblicza macierz 6 parametrów (a, b, c, d, e, f) dla PIL.Image.Transform.AFFINE,
        która mapuje współrzędne docelowe (dst) na źródłowe (src).
        """
        (x0, y0), (x1, y1), (x2, y2) = dst_pts
        (u0, v0), (u1, v1), (u2, v2) = src_pts

        det = (x0 - x2) * (y1 - y2) - (x1 - x2) * (y0 - y2)
        if abs(det) < 1e-8:
            return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

        a = ((u0 - u2) * (y1 - y2) - (u1 - u2) * (y0 - y2)) / det
        b = ((u1 - u2) * (x0 - x2) - (u0 - u2) * (x1 - x2)) / det
        c = u2 - a * x2 - b * y2

        d = ((v0 - v2) * (y1 - y2) - (v1 - v2) * (y0 - y2)) / det
        e = ((v1 - v2) * (x0 - x2) - (v0 - v2) * (x1 - x2)) / det
        f = v2 - d * x2 - e * y2

        return (a, b, c, d, e, f)

    def render(
        self,
        model_loc: str,
        size: int = 256,
        rot_y: float = -45.0,
        rot_x: float = 30.0,
        block_rot_y: float = 0.0,
        block_rot_x: float = 0.0,
        tint_rgb: tuple[int, int, int] | None = None,
    ) -> Image.Image:
        """Renderuje model bloku do obrazu RGBA o wymiarach size x size."""
        model = self.resolve_model(model_loc)
        textures_map = model["textures"]
        elements = model["elements"]

        # Skala dopasowująca sześcian 16x16x16 do płótna z niewielkim marginesem
        scale = size / 28.0
        offset = size / 2.0

        render_faces = []

        for elem in elements:
            verts_3d = self._get_box_vertices(elem)
            proj = [
                self._project_to_camera(v, rot_y, rot_x, block_rot_y, block_rot_x)
                for v in verts_3d
            ]

            for face_name, face_data in elem.get("faces", {}).items():
                tex_ref = face_data.get("texture", "")
                if tex_ref.startswith("#"):
                    tex_loc = textures_map.get(tex_ref[1:])
                else:
                    tex_loc = tex_ref

                if not tex_loc:
                    continue

                idx_tl, idx_tr, idx_bl, idx_br = FACE_VERTICES[face_name]
                p_tl, p_tr, p_bl, p_br = proj[idx_tl], proj[idx_tr], proj[idx_bl], proj[idx_br]

                # Backface culling (iloczyn wektorowy 2D sprawdza czy ściana jest przodem do kamery)
                ux, uy = p_tr[0] - p_tl[0], p_tr[1] - p_tl[1]
                vx, vy = p_bl[0] - p_tl[0], p_bl[1] - p_tl[1]
                cross_z = ux * vy - uy * vx
                if cross_z <= 0:
                    continue

                # Średnia głębokość Z ściany do sortowania (algorytm malarza)
                avg_depth = (p_tl[2] + p_tr[2] + p_bl[2] + p_br[2]) / 4.0

                # Pobieranie tekstury (z opcjonalnym tintem dla tintindex >= 0)
                use_tint = tint_rgb if "tintindex" in face_data else None
                tex_img = self.get_texture(tex_loc, use_tint)
                tw, th = tex_img.size

                # Współrzędne UV w Minecraft są w zakresie 0..16
                uv = face_data.get("uv")
                if uv is None:
                    # Domyślne UV z pozycji elementu, jeśli nie podano jawnie w JSON
                    x1, y1, z1 = elem["from"]
                    x2, y2, z2 = elem["to"]
                    if face_name in ("north", "south"):
                        uv = [x1, 16 - y2, x2, 16 - y1]
                    elif face_name in ("east", "west"):
                        uv = [z1, 16 - y2, z2, 16 - y1]
                    else:
                        uv = [x1, z1, x2, z2]

                u1, v1, u2, v2 = [c * (tw / 16.0) for c in uv]

                # Punkty UV na teksturze (z obsługą obrotu UV w pliku JSON: 90, 180, 270)
                uv_corners = [(u1, v1), (u2, v1), (u1, v2), (u2, v2)]
                uv_rot = face_data.get("rotation", 0)
                if uv_rot == 90:
                    uv_corners = [uv_corners[2], uv_corners[0], uv_corners[3], uv_corners[1]]
                elif uv_rot == 180:
                    uv_corners = [uv_corners[3], uv_corners[2], uv_corners[1], uv_corners[0]]
                elif uv_rot == 270:
                    uv_corners = [uv_corners[1], uv_corners[3], uv_corners[0], uv_corners[2]]

                # Współrzędne ekranowe 2D (TL, TR, BL, BR)
                screen_pts = [
                    (p_tl[0] * scale + offset, p_tl[1] * scale + offset),
                    (p_tr[0] * scale + offset, p_tr[1] * scale + offset),
                    (p_bl[0] * scale + offset, p_bl[1] * scale + offset),
                    (p_br[0] * scale + offset, p_br[1] * scale + offset),
                ]

                render_faces.append({
                    "depth": avg_depth,
                    "face_name": face_name,
                    "tex_img": tex_img,
                    "uv_pts": uv_corners[:3],      # TL, TR, BL
                    "screen_pts": screen_pts[:3],  # TL, TR, BL
                    "all_screen": screen_pts,
                })

        # Sortujemy od najdalszych ścian (najmniejsze Z) do najbliższych (największe Z)
        render_faces.sort(key=lambda item: item["depth"])

        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        for item in render_faces:
            # Nakładanie cieniowania kierunkowego na teksturę
            shade = FACE_SHADING.get(item["face_name"], 1.0)
            tex = item["tex_img"]
            if shade < 1.0:
                tex = ImageEnhance.Brightness(tex).enhance(shade)

            matrix = self._solve_affine(item["uv_pts"], item["screen_pts"])

            # Nearest-neighbor zachowuje ostre piksele Minecrafta
            warped = tex.transform(
                (size, size),
                Image.Transform.AFFINE,
                matrix,
                resample=Image.Resampling.NEAREST,
            )

            # Maska wielokąta zapobiega wyciekaniu pikseli poza obrys ściany
            mask = Image.new("L", (size, size), 0)
            tl, tr, bl, br = item["all_screen"]
            ImageDraw.Draw(mask).polygon([tl, tr, br, bl], fill=255)

            # Łączymy alfę maski z alfą samej tekstury (np. dla szkła lub liści)
            r, g, b, a = warped.split()
            final_alpha = ImageChops.multiply(a, mask)
            warped.putalpha(final_alpha)

            canvas = Image.alpha_composite(canvas, warped)

        return canvas
