from bs4 import BeautifulSoup
import openpyxl
filename = r"resource-development\rd4"
htmlpage = filename + ".html"
output = filename + ".xlsx"

# ---- Load HTML ----
with open(htmlpage, "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f, "html.parser")

# ---- Excel Setup ----
wb = openpyxl.Workbook()
ws = wb.active
ws.append(["Ability Name", "TTP", "Status", "Command", "Output", "Error"])

# ---- Locate the table ----
table = soup.find("table", {"id": "link-table"})
rows = table.find_all("tr")

for row in rows:
    cols = row.find_all("td")
    if len(cols) < 8:
        continue  # skip headers or malformed rows

    # -------- Extract core columns --------
    status = cols[1].get_text(strip=True)
    ability_name = cols[2].get_text(strip=True)
    ttp = cols[3].get_text(strip=True)

    # -------- Extract Command text --------
    cmd_pre = row.select_one("div.dropdown-menu .dropdown-content .box pre")
    command = cmd_pre.get_text("\n", strip=True) if cmd_pre else ""

    # -------- Extract Output --------
    output_span = row.select_one("div.dropdown-menu .dropdown-content span.is-family-monospace")
    standard_output = output_span.get_text(strip=True) if output_span else ""

    # ---- Extract Standard Error ----
    error_text = ""
    error_label = row.find("label", string=lambda s: s and "Standard Error" in s)

    if error_label:
        error_box = error_label.find_next("div", class_="box")
        if error_box:
            pre = error_box.find("pre")
            if pre:
                error_text = pre.get_text("\n", strip=True)


    # -------- Save to Excel --------
    ws.append([ability_name, ttp, status, command, standard_output, error_text])

# ---- Save file ----
wb.save(output)
print(f"Saved to {output}")
