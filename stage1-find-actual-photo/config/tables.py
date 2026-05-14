# =====================================================================
#  AIRTABLE TABLE DEFINITIONS
#  Each entry maps a table name + ID to its blend prompt.
# =====================================================================

_BLEND_TEMPLATE = (
    "Seamlessly integrate {fixtures} into this {room}. "
    "Ensure both fixtures are realistically scaled and proportionate to "
    "the room size and surrounding {surroundings}. Match the perspective, "
    "lighting, and shadows naturally so they appear as if originally part "
    "of the space."
)


def _table(name: str, table_id: str, fixtures: str,
           room: str = "room", surroundings: str = "furniture") -> dict:
    """Helper to build a table entry with a consistent blend prompt."""
    return {
        "name": name,
        "id": table_id,
        "blend_prompt": _BLEND_TEMPLATE.format(
            fixtures=fixtures, room=room, surroundings=surroundings
        ),
    }


AIRTABLE_TABLES = [
    # ── Floor Lamps ─────────────────────────────────────────────────
    _table("Floor Lamp and Ceiling Mounted",       "tbl0H4CE8jdcawJfT",
           "a floor lamp and ceiling-mounted light"),
    _table("Floor Lamp and Chandelier",            "tblUdcQhWKBbj2QG2",
           "a floor lamp and chandelier"),

    # ── Flush Ceiling Lights ────────────────────────────────────────
    _table("Flush Ceiling Lights and Wall Lights",  "tblYrmZTWRBzQnBFd",
           "a flush ceiling light and wall light"),
    _table("Flush Ceiling Lights and Table Lamps",  "tblYTf4qZJ6UHGAPN",
           "a flush ceiling light and table lamp"),
    _table("Flush Ceiling Lights and Floor Lamps",  "tblnvjkVkL0BpWfTR",
           "a flush ceiling light and floor lamp"),

    # ── Semi Ceiling Lights ─────────────────────────────────────────
    _table("Semi Ceiling Lights and Wall Lights",   "tblozD79qS5YuJXXv",
           "a semi ceiling light and wall light"),
    _table("Semi Ceiling Lights and Table Lamps",   "tblFbDpIVCKCX3Mpf",
           "a semi ceiling light and table lamp"),
    _table("Semi Ceiling Lights and Floor Lamps",   "tblbhmfLEjC62rOH6",
           "a semi ceiling light and floor lamp"),

    # ── Chandeliers ─────────────────────────────────────────────────
    _table("Chandelier and Wall Lights",            "tblTL9xe4fLuAa96W",
           "a chandelier and wall light"),
    _table("Chandelier and Table Lamps",            "tblvkeFCfcKYH9hPg",
           "a chandelier and table lamp"),
    _table("Chandelier and Floor Lamps",            "tblpdaohkCjyypjZO",
           "a chandelier and floor lamp"),

    # ── Cluster Chandeliers ─────────────────────────────────────────
    _table("Cluster Chandelier and Wall Lights",    "tblWcWGHhaJBmxCSl",
           "a cluster chandelier and wall light"),
    _table("Cluster Chandelier and Table Lamps",    "tblvLVkVGaoKuVxCA",
           "a cluster chandelier and table lamp"),
    _table("Cluster Chandelier and Floor Lamps",    "tblVeh75sIJfIje07",
           "a cluster chandelier and floor lamp"),

    # ── Pendant Lights ──────────────────────────────────────────────
    _table("Pendant Lights and Wall Lights",        "tblBi9yHI2fEq7KTD",
           "a pendant light and wall light"),
    _table("Pendant Lights and Table Lamps",        "tblev1cIxtxBAlQ4o",
           "a pendant light and table lamp"),
    _table("Pendant Lights and Floor Lamps",        "tbl086DTyCgaKVe0j",
           "a pendant light and floor lamp"),

    # ── Bathroom ────────────────────────────────────────────────────
    _table("Bathroom Lights + Chandelier",          "tblbNOklr1NPwsoMO",
           "a bathroom light and chandelier",
           room="bathroom", surroundings="fixtures"),
    _table("Bathroom Lights + Pendant Light",       "tbloulRxtH5w4eG0p",
           "a bathroom light and pendant light",
           room="bathroom", surroundings="fixtures"),
]
