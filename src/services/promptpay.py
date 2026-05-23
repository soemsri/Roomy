def crc16_xmodem(data: str) -> str:
    """
    Calculate CRC16 XMODEM (CCITT) - Standard for EMVCo/PromptPay
    """
    crc = 0xFFFF
    for char in data:
        crc ^= ord(char) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"

def format_tag(tag: str, value: str) -> str:
    """Format Tag (ID + Length + Value)"""
    return f"{tag}{len(value):02d}{value}"

def generate_promptpay_payload(id_number: str, amount: float = None):
    """
    Generate PromptPay Payload following EMVCo Standard
    id_number: Phone (08xxxxxxx) or National ID (13 digits)
    amount: Baht (optional)
    """
    # 1. Clean ID
    id_number = id_number.replace("-", "").replace(" ", "")
    
    # 2. Build Merchant Account Info (Tag 29)
    # AID for PromptPay is 'A000000677010111'
    aid = "A000000677010111"
    
    if len(id_number) == 13: # National ID
        account_value = format_tag("00", aid) + format_tag("02", id_number)
    else: # Phone Number (convert to international format 0066...)
        phone = "0066" + id_number[1:] if id_number.startswith("0") else id_number
        account_value = format_tag("00", aid) + format_tag("01", phone)
        
    merchant_info = format_tag("29", account_value)
    
    # 3. Assemble Payload
    payload = ""
    payload += format_tag("00", "01")          # Payload Format Indicator
    payload += format_tag("01", "11" if amount is None else "12") # Initiation Method
    payload += merchant_info                   # Merchant Account Info
    payload += format_tag("53", "764")         # Currency (THB)
    
    if amount is not None:
        amount_str = f"{amount:.2f}"
        payload += format_tag("54", amount_str)
        
    payload += format_tag("58", "TH")          # Country Code
    payload += "6304"                          # CRC Tag + Length (04)
    
    # 4. Calculate CRC
    crc = crc16_xmodem(payload)
    return payload + crc

if __name__ == "__main__":
    # Test case (Standard static QR)
    print(f"Test static: {generate_promptpay_payload('0812345678')}")
    # Test case (Standard dynamic QR with amount)
    print(f"Test amount: {generate_promptpay_payload('0812345678', 100.50)}")
