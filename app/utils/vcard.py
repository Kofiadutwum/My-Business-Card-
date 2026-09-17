"""vCard generation.

Two decisions here are deliberate and worth defending:

1.  Version 3.0, not 4.0. Android and older iOS import 3.0 without
    complaint; 4.0 support is still patchy on the handsets your customers
    actually carry. Compatibility beats modernity for a file whose entire
    job is to open on a stranger's phone.

2.  The file is self-contained. Every field is embedded in the .vcf itself
    rather than pointing back at the website. The recipient can save the
    contact with no internet connection and no app installed, which is the
    behaviour that makes a digital card feel as quick as a paper one.

RFC 6350 requires CRLF line endings. Getting this wrong produces files that
import as a single mangled contact on some devices.
"""

CRLF = "\r\n"


def escape(value):
    """Escape the four characters that carry meaning inside a vCard value."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line):
    """Fold lines longer than 75 octets, per the spec.

    Continuation lines begin with a single space. Long bios and long URLs
    are the usual triggers.
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 74:
            chunks.append(current)
            current = " " + char
        else:
            current += char
    chunks.append(current)
    return CRLF.join(chunks)


def split_name(full_name):
    """Best-effort split into family / given / additional names.

    Ghanaian naming does not always follow given-then-family order, and a
    name such as 'Kwame Osei Mensah' is genuinely ambiguous. The FN field
    always carries the full name exactly as typed, so display is never
    wrong even when this structural guess is.
    """
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", parts[0], ""
    family = parts[-1]
    given = parts[0]
    additional = " ".join(parts[1:-1])
    return family, given, additional


def build_vcard(profile, card_url=None, photo_url=None):
    """Render a Profile as a vCard 3.0 string."""
    family, given, additional = split_name(profile.full_name)

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{escape(family)};{escape(given)};{escape(additional)};;",
        f"FN:{escape(profile.full_name)}",
    ]

    if profile.organisation:
        lines.append(f"ORG:{escape(profile.organisation)}")
    if profile.job_title:
        lines.append(f"TITLE:{escape(profile.job_title)}")
    if profile.phone:
        lines.append(f"TEL;TYPE=CELL,VOICE:{escape(profile.phone)}")
    if profile.whatsapp and profile.whatsapp != profile.phone:
        lines.append(f"TEL;TYPE=CELL:{escape(profile.whatsapp)}")
    if profile.email:
        lines.append(f"EMAIL;TYPE=INTERNET,WORK:{escape(profile.email)}")
    if profile.website:
        lines.append(f"URL:{escape(profile.website)}")
    if profile.location:
        # ADR has seven semicolon-separated components; locality is the third.
        lines.append(f"ADR;TYPE=WORK:;;;{escape(profile.location)};;;")
    if profile.bio:
        lines.append(f"NOTE:{escape(profile.bio)}")
    if photo_url:
        lines.append(f"PHOTO;VALUE=URI:{escape(photo_url)}")

    for link in profile.social_links:
        lines.append(
            f"X-SOCIALPROFILE;TYPE={escape(link.platform)}:{escape(link.url)}"
        )

    if card_url:
        lines.append(f"URL;TYPE=card:{escape(card_url)}")

    lines.append("END:VCARD")
    return CRLF.join(fold(line) for line in lines) + CRLF


def vcard_filename(profile):
    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in (profile.full_name or "contact")
    )
    return f"{safe.strip('-').lower() or 'contact'}.vcf"
