import os
import logging
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def draw_chat_icon(draw, center_x, center_y, size, color):
    w, h = int(size * 1.15), int(size * 0.82)
    x1, y1 = center_x - w // 2, center_y - h // 2
    x2, y2 = center_x + w // 2, center_y + h // 2
    draw.rounded_rectangle([x1, y1, x2, y2], radius=32, fill=color)
    # tail
    tail_pts = [(center_x - 35, y2 - 5), (center_x - 70, y2 + 40), (center_x + 10, y2 - 5)]
    draw.polygon(tail_pts, fill=color)
    # dots
    dot_r = 14
    for offset in [-50, 0, 50]:
        draw.ellipse([center_x + offset - dot_r, center_y - dot_r, center_x + offset + dot_r, center_y + dot_r], fill=(255, 255, 255))

def draw_rules_icon(draw, center_x, center_y, size, color):
    w, h = int(size * 0.88), int(size * 1.15)
    x1, y1 = center_x - w // 2, center_y - h // 2
    x2, y2 = center_x + w // 2, center_y + h // 2
    draw.rounded_rectangle([x1, y1, x2, y2], radius=24, fill=color)
    # Inner lines
    line_color = (255, 255, 255)
    margin = 38
    for i, offset_y in enumerate([-45, 0, 45, 90]):
        line_w = w - (margin * 2) if i < 3 else int((w - margin * 2) * 0.6)
        draw.rounded_rectangle([x1 + margin, center_y + offset_y - 7, x1 + margin + line_w, center_y + offset_y + 7], radius=7, fill=line_color)
    # Seal/Badge circle at top right
    draw.ellipse([x2 - 55, y1 + 18, x2 - 15, y1 + 58], fill=(245, 158, 11))

def draw_booking_icon(draw, center_x, center_y, size, color):
    w, h = int(size * 1.05), int(size * 1.0)
    x1, y1 = center_x - w // 2, center_y - h // 2
    x2, y2 = center_x + w // 2, center_y + h // 2
    
    # House body
    draw.rounded_rectangle([x1, y1 + 45, x2, y2], radius=26, fill=color)
    # Roof
    roof_pts = [(center_x, y1 - 25), (x1 - 20, y1 + 55), (x2 + 20, y1 + 55)]
    draw.polygon(roof_pts, fill=color)
    # Door
    door_w, door_h = 70, 105
    draw.rounded_rectangle([center_x - door_w//2, y2 - door_h, center_x + door_w//2, y2], radius=18, fill=(255, 255, 255))
    # Plus badge
    draw.ellipse([x2 - 45, y1 + 45, x2 + 10, y1 + 100], fill=(16, 185, 129))
    draw.line([x2 - 17, y1 + 58, x2 - 17, y1 + 88], fill=(255, 255, 255), width=7)
    draw.line([x2 - 32, y1 + 73, x2 - 2, y1 + 73], fill=(255, 255, 255), width=7)

def find_thai_font(size=95):
    font_candidates = [
        "C:\\Windows\\Fonts\\tahoma.ttf",
        "C:\\Windows\\Fonts\\leelawdb.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "/usr/share/fonts/truetype/tlwg/Garuda-Bold.ttf",
        "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
        "/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf",
        "/usr/share/fonts/truetype/tlwg/Loma.ttf",
        "/usr/share/fonts/truetype/tlwg/Waree-Bold.ttf",
        "/usr/share/fonts/truetype/tlwg/Waree.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    for p in font_candidates:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                logger.info(f"Using Thai font: {p}")
                return f
            except Exception as e:
                logger.warning(f"Failed to load {p}: {e}")
    return ImageFont.load_default()

def create_3btn_rich_menu(output_path="src/tenant_richmenu.jpg"):
    width, height = 2500, 843
    grid_w = width // 3 # 833
    
    # Modern clean light gradient background
    img = Image.new('RGB', (width, height), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        r = int(248 + (235 - 248) * (y / height))
        g = int(250 + (242 - 250) * (y / height))
        b = int(252 + (250 - 252) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    font_thai = find_thai_font(size=100)

    buttons = [
        {
            "thai": "สนทนา",
            "icon": draw_chat_icon,
            "accent": (37, 99, 235), # Royal Blue
        },
        {
            "thai": "กฎระเบียบ",
            "icon": draw_rules_icon,
            "accent": (2, 132, 199), # Sky Blue
        },
        {
            "thai": "จองห้องพัก",
            "icon": draw_booking_icon,
            "accent": (16, 185, 129), # Emerald Green
        }
    ]

    card_margin_x = 24
    card_margin_y = 28
    card_radius = 40

    for i, btn in enumerate(buttons):
        x1 = i * grid_w + card_margin_x
        y1 = card_margin_y
        x2 = (i + 1) * grid_w - card_margin_x if i < 2 else width - card_margin_x
        y2 = height - card_margin_y
        card_w = x2 - x1
        center_x = x1 + card_w // 2

        # Card shadow
        draw.rounded_rectangle(
            [x1, y1 + 10, x2, y2 + 10],
            radius=card_radius,
            fill=(215, 225, 238)
        )

        # White Card base
        draw.rounded_rectangle(
            [x1, y1, x2, y2],
            radius=card_radius,
            fill=(255, 255, 255),
            outline=(226, 232, 240),
            width=3
        )

        # Icon Background Circle
        icon_bg_size = 250
        icon_bg_y = y1 + 75
        icon_center_y = icon_bg_y + icon_bg_size // 2
        
        acc_r, acc_g, acc_b = btn["accent"]
        soft_fill = (
            int(acc_r * 0.12 + 255 * 0.88),
            int(acc_g * 0.12 + 255 * 0.88),
            int(acc_b * 0.12 + 255 * 0.88)
        )
        draw.ellipse(
            [center_x - icon_bg_size//2, icon_bg_y, center_x + icon_bg_size//2, icon_bg_y + icon_bg_size],
            fill=soft_fill
        )

        # Draw specific icon
        btn["icon"](draw, center_x, icon_center_y, 140, btn["accent"])

        # Main Thai Label (Centered)
        thai_text = btn["thai"]
        bbox_th = draw.textbbox((0, 0), thai_text, font=font_thai)
        tw = bbox_th[2] - bbox_th[0]
        text_y = y1 + 420
        draw.text((center_x - tw // 2, text_y), thai_text, fill=(15, 23, 42), font=font_thai)

        # Bottom accent indicator pill
        pill_w = 140
        draw.rounded_rectangle(
            [center_x - pill_w // 2, y2 - 45, center_x + pill_w // 2, y2 - 30],
            radius=8,
            fill=btn["accent"]
        )

    # Save output
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if os.path.dirname(output_path) else None
    img.save(output_path, quality=95)
    logger.info(f"Generated clean Thai 3-button rich menu at: {output_path}")

if __name__ == "__main__":
    create_3btn_rich_menu()
