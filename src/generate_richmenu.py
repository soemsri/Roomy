from PIL import Image, ImageDraw, ImageFont
import os

def draw_icon(draw, icon_type, x, y, size, color):
    """Draws simple geometric icons for the menu."""
    padding = size // 4
    ix, iy = x + padding, y + padding
    isize = size - (2 * padding)
    
    if icon_type == "room": # Grid icon
        step = isize // 3
        for i in range(3):
            for j in range(3):
                draw.rectangle([ix + i*step + 2, iy + j*step + 2, ix + (i+1)*step - 2, iy + (j+1)*step - 2], outline=color, width=3)
    
    elif icon_type == "meter": # Gauge icon
        draw.arc([ix, iy, ix + isize, iy + isize], start=180, end=0, fill=color, width=5)
        draw.line([ix + isize//2, iy + isize//2, ix + isize, iy + isize//2], fill=color, width=5)
        draw.ellipse([ix + isize//2 - 5, iy + isize//2 - 5, ix + isize//2 + 5, iy + isize//2 + 5], fill=color)

    elif icon_type == "income": # Coin/Money icon
        draw.ellipse([ix, iy, ix + isize, iy + isize], outline=color, width=5)
        # Draw a simple Baht symbol or just a circle with a line
        draw.line([ix + isize//2, iy + padding, ix + isize//2, iy + isize - padding], fill=color, width=5)

    elif icon_type == "lease": # Document icon
        draw.rectangle([ix + 10, iy, ix + isize - 10, iy + isize], outline=color, width=5)
        for i in range(3):
            draw.line([ix + 20, iy + 25 + i*20, ix + isize - 20, iy + 25 + i*20], fill=color, width=3)

    elif icon_type == "settings": # Gear icon
        draw.ellipse([ix + isize//4, iy + isize//4, ix + 3*isize//4, iy + 3*isize//4], outline=color, width=15)
        for i in range(8):
            import math
            angle = i * (360 / 8)
            rad = math.radians(angle)
            x1 = ix + isize//2 + math.cos(rad) * (isize//4)
            y1 = iy + isize//2 + math.sin(rad) * (isize//4)
            x2 = ix + isize//2 + math.cos(rad) * (isize//2)
            y2 = iy + isize//2 + math.sin(rad) * (isize//2)
            draw.line([x1, y1, x2, y2], fill=color, width=10)

    elif icon_type == "repair": # Wrench/Tool icon
        draw.line([ix, iy + isize, ix + isize, iy], fill=color, width=15)
        draw.ellipse([ix + isize - 30, iy, ix + isize, iy + 30], fill=color)

def create_rich_menu_pro(filename, title, buttons, icons):
    # LINE Rich Menu Standard Size
    width, height = 2500, 1686
    grid_w, grid_h = width // 3, height // 2
    
    # Elegant Color Palette (Owner: Professional Dark Blue)
    bg_color = (28, 40, 51)
    card_color = (40, 55, 71)
    accent_color = (52, 152, 219) # Blue accent
    text_color = (255, 255, 255)
    
    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Try to load a nice font
    font_path = "C:\\Windows\\Fonts\\tahoma.ttf" # Common on Windows
    if not os.path.exists(font_path):
        font_path = "arial.ttf"
        
    try:
        font_main = ImageFont.truetype(font_path, 70)
        font_sub = ImageFont.truetype(font_path, 45)
    except:
        font_main = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    for i, (thai_text, eng_text) in enumerate(buttons):
        row = i // 3
        col = i % 3
        x1, y1 = col * grid_w, row * grid_h
        x2, y2 = x1 + grid_w, y1 + grid_h
        
        # Draw Card Background (with small margin)
        margin = 15
        draw.rectangle([x1 + margin, y1 + margin, x2 - margin, y2 - margin], fill=card_color, outline=accent_color, width=2)
        
        # Draw Icon
        icon_size = 300
        draw_icon(draw, icons[i], x1 + (grid_w - icon_size)//2, y1 + 100, icon_size, accent_color)
        
        # Draw Thai Text
        bbox = draw.textbbox((0, 0), thai_text, font=font_main)
        tw = bbox[2] - bbox[0]
        draw.text((x1 + (grid_w - tw) // 2, y1 + 450), thai_text, fill=text_color, font=font_main)
        
        # Draw English Text
        bbox_en = draw.textbbox((0, 0), eng_text, font=font_sub)
        tew = bbox_en[2] - bbox_en[0]
        draw.text((x1 + (grid_w - tew) // 2, y1 + 550), eng_text, fill=(171, 178, 185), font=font_sub)

    # Save the image
    img.save(filename)
    print(f"Professional Rich Menu Created: {filename}")

if __name__ == "__main__":
    # Owner Rich Menu Config
    owner_btns = [
        ("ผังห้อง", "Room Map"), 
        ("จดมิเตอร์", "Meters"), 
        ("สรุปรายรับ", "Income"),
        ("จัดการสัญญา", "Leases"), 
        ("ตั้งค่า", "Settings"), 
        ("รายการแจ้งซ่อม", "Admin Repair")
    ]
    owner_icons = ["room", "meter", "income", "lease", "settings", "repair"]
    
    create_rich_menu_pro("owner_richmenu.png", "Owner Menu", owner_btns, owner_icons)
