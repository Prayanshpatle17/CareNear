# CareNear

A web app that helps people in a city find hospitals, see which beds are free, check doctors' OPD timings, and request appointments.

> **Status: learning project / prototype.** All hospitals, doctors and phone numbers in this repository are **made-up sample data**. Do not use it with real patient data or rely on it in an emergency. In an emergency, call **112** (or **108** for an ambulance).

## Why

In an urgent moment, people call hospital after hospital to find a bed, and bed counts go out of date quickly. CareNear tries to put hospital information, bed availability and doctor schedules in one place, and to be **honest about how fresh the data is**.

## Features

**Public page**
- Search hospitals by name or area, and filter by specialty
- Bed counts (general, ICU, ventilator), each with a "last updated" time and an "may be outdated" warning after 2 hours
- "I need a bed" filter: hospitals with a reported free bed come first; hospitals that have not reported stay visible with a "call to confirm" note
- Map view with colour-coded pins, popups with a Call link and Directions
- "Near me": sorts by distance using your browser's location (it never leaves your device)
- Doctors with OPD days, timings and an "OPD today" status
- Appointment *requests* (not bookings): the hospital calls the patient back to confirm

**Admin page (hospital staff, protected by a secret key)**
- Update bed counts
- Set a hospital's location by clicking on a map
- Add doctors and mark them on leave
- "Needs attention" list: hospitals with no or outdated bed numbers
- Accept, decline or delete appointment requests

## Design choices

- Beds that were never reported show a dash, never a fake zero
- Filters never hide a hospital that has not reported
- Appointment requests collect only a name, mobile number and date: no medical details
- Changing data needs an admin key; reading data is public

## Tech stack

- **Frontend:** HTML, CSS, JavaScript, [Leaflet](https://leafletjs.com/) with OpenStreetMap
- **Backend:** Python, [FastAPI](https://fastapi.tiangolo.com/)
- **Database:** SQLite

## Quick start

You need **Python 3.9 or newer** (developed and tested on 3.12).

```bash
# 1. Install the dependencies
pip install -r requirements.txt

# 2. Start the server (run this from the project folder)
uvicorn main:app --reload
```

Then open:

- Public page: <http://127.0.0.1:8000/app>
- Admin page: <http://127.0.0.1:8000/admin>
- API documentation: <http://127.0.0.1:8000/docs>

Open the pages from these addresses rather than double-clicking the HTML files. The map and the location features need a normal `http://` address.

On the first run the app creates `hospitals.db` and fills it with sample data. It also creates `admin_key.txt` containing a random admin key. Paste that key into the admin page to unlock it. You can also set your own key with the `ADMIN_KEY` environment variable.

**Keep `admin_key.txt` and `hospitals.db` private.** Both are listed in `.gitignore` so they are never committed.

## Project structure

```
main.py            Backend: database setup and all API endpoints
index.html         Public page
admin.html         Admin page
requirements.txt   Python dependencies
```

## Database

| Table | What it holds |
|---|---|
| `hospitals` | Name, area, address, phone, specialties, latitude, longitude |
| `beds` | General / ICU / ventilator counts and the last-updated time |
| `doctors` | Specialty, OPD days and timings, on-leave flag |
| `appointments` | Patient name, phone, preferred date, status |

## API overview

Endpoints marked **key** need the `X-Admin-Key` header.

| Method | Path | Purpose |
|---|---|---|
| GET | `/hospitals` | List hospitals (`?search=` and `?specialty=`) |
| GET | `/hospitals/{id}` | One hospital |
| PUT | `/hospitals/{id}/beds` | Update bed counts (**key**) |
| PUT | `/hospitals/{id}/location` | Set the map location (**key**) |
| GET | `/hospitals/{id}/doctors` | Doctors of a hospital |
| POST | `/hospitals/{id}/doctors` | Add a doctor (**key**) |
| PUT | `/doctors/{id}/leave` | Mark a doctor on leave or back (**key**) |
| POST | `/doctors/{id}/appointments` | Send an appointment request |
| GET | `/admin/appointments` | List requests (**key**) |
| PUT | `/admin/appointments/{id}` | Accept or decline (**key**) |
| DELETE | `/admin/appointments/{id}` | Delete a request (**key**) |
| GET | `/admin/stale` | Hospitals with missing or outdated beds (**key**) |
| GET | `/admin/check` | Check that an admin key is valid (**key**) |

## Limitations

This is a prototype, not a production system:

- Live bed data depends on hospitals keeping it updated, and the sample data is fictional
- There is one shared admin key (no separate login per hospital)
- No HTTPS, CORS is open to everyone, and spam protection is basic
- The map uses OpenStreetMap's free tile server, which is meant for light use only
- Storing real patients' names and phone numbers would need a privacy notice and a data retention plan

## Roadmap

- [x] Hospital directory with search and filters
- [x] Beds with "last updated" and stale-data warnings
- [x] Doctors and OPD timings
- [x] Admin panel and appointment requests
- [x] Map view and "Near me"
- [ ] AI assistant to help patients find a bed
- [ ] Reminders for hospitals to update their numbers
- [ ] Separate login for each hospital
- [ ] Real hospital data and partnerships

## License

Released under the [MIT License](LICENSE).
