from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import sqlite3, base64, uuid, calendar, json, urllib.request, urllib.error, ssl, shutil, threading
from datetime import datetime, date, timedelta
from pathlib import Path
from io import BytesIO

SOURCE_DIR = Path(__file__).resolve().parent
BASE_DIR = SOURCE_DIR
DATA_DIR = SOURCE_DIR
DB_PATH = DATA_DIR / 'autonoleggio.db'
STATIC_DIR = DATA_DIR / 'static'
UPLOAD_DIR = STATIC_DIR / 'uploads'
SIGN_DIR = STATIC_DIR / 'signatures'

app = Flask(__name__, template_folder=str(SOURCE_DIR / 'templates'), static_folder=str(STATIC_DIR))
app.secret_key = 'cambia-questa-chiave-in-produzione'
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def ensure_column(conn, table, column, definition):
    cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def init_db():
    conn = get_db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plate TEXT UNIQUE NOT NULL, brand TEXT NOT NULL, model TEXT NOT NULL,
        category TEXT, daily_rate REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'Disponibile',
        mileage INTEGER NOT NULL DEFAULT 0, notes TEXT
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL, last_name TEXT NOT NULL, phone TEXT, email TEXT,
        document_id TEXT, license_number TEXT, notes TEXT
    );
    CREATE TABLE IF NOT EXISTS rentals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
        start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Prenotato', total REAL NOT NULL DEFAULT 0, deposit REAL NOT NULL DEFAULT 0,
        pickup_km INTEGER DEFAULT 0, return_km INTEGER, notes TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id), FOREIGN KEY(customer_id) REFERENCES customers(id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rental_id INTEGER NOT NULL,
        amount REAL NOT NULL, method TEXT NOT NULL, paid_at TEXT NOT NULL, reference TEXT, notes TEXT,
        FOREIGN KEY(rental_id) REFERENCES rentals(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS vehicle_deadlines (
        id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL,
        kind TEXT NOT NULL, due_date TEXT NOT NULL, amount REAL DEFAULT 0, notes TEXT, completed INTEGER DEFAULT 0,
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS damages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER NOT NULL, rental_id INTEGER,
        damage_date TEXT NOT NULL, description TEXT NOT NULL, cost REAL DEFAULT 0, status TEXT DEFAULT 'Aperto', photo TEXT,
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE,
        FOREIGN KEY(rental_id) REFERENCES rentals(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS cargos_settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        username TEXT, password TEXT, apikey TEXT,
        agency_id TEXT, agency_name TEXT, agency_place_code TEXT, agency_address TEXT, agency_phone TEXT,
        api_base TEXT DEFAULT 'https://cargos.poliziadistato.it/CARGOS_API/'
    );
    CREATE TABLE IF NOT EXISTS cargos_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rental_id INTEGER NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL, transaction_id TEXT, error_message TEXT,
        record_text TEXT, response_json TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY(rental_id) REFERENCES rentals(id) ON DELETE CASCADE
    );
    INSERT OR IGNORE INTO cargos_settings(id,api_base) VALUES(1,'https://cargos.poliziadistato.it/CARGOS_API/');
    ''')
    ensure_column(conn, 'customers', 'address', 'TEXT')
    ensure_column(conn, 'customers', 'birth_date', 'TEXT')
    ensure_column(conn, 'vehicles', 'insurance_due', 'TEXT')
    ensure_column(conn, 'vehicles', 'inspection_due', 'TEXT')
    ensure_column(conn, 'vehicles', 'tax_due', 'TEXT')
    ensure_column(conn, 'rentals', 'signature', 'TEXT')
    ensure_column(conn, 'rentals', 'fuel_out', 'TEXT')
    ensure_column(conn, 'rentals', 'fuel_in', 'TEXT')
    for col, definition in [
        ('birth_place_code','TEXT'),('citizenship_code','TEXT'),('residence_place_code','TEXT'),
        ('document_type_code','TEXT'),('document_issue_place_code','TEXT'),('license_issue_place_code','TEXT')
    ]: ensure_column(conn,'customers',col,definition)
    for col, definition in [('cargos_vehicle_type','TEXT'),('color','TEXT'),('gps','INTEGER DEFAULT 0'),('engine_block','INTEGER DEFAULT 0')]:
        ensure_column(conn,'vehicles',col,definition)
    for col, definition in [
        ('pickup_time',"TEXT DEFAULT '09:00'"),('return_time',"TEXT DEFAULT '09:00'"),('cargos_payment_type','TEXT'),
        ('checkout_place_code','TEXT'),('checkout_address','TEXT'),('checkin_place_code','TEXT'),('checkin_address','TEXT'),('operator_id','TEXT')
    ]: ensure_column(conn,'rentals',col,definition)
    conn.commit(); conn.close()


def rental_join(conn, where='', args=()):
    return conn.execute(f'''SELECT r.*, v.brand, v.model, v.plate, v.category, v.daily_rate,
        v.cargos_vehicle_type, v.color, v.gps, v.engine_block,
        c.first_name, c.last_name, c.phone, c.email, c.document_id, c.license_number, c.address, c.birth_date,
        c.birth_place_code, c.citizenship_code, c.residence_place_code, c.document_type_code,
        c.document_issue_place_code, c.license_issue_place_code
        FROM rentals r JOIN vehicles v ON v.id=r.vehicle_id JOIN customers c ON c.id=r.customer_id {where}''', args)


def save_signature(data_url):
    if not data_url or ',' not in data_url: return None
    try:
        encoded = data_url.split(',', 1)[1]
        name = f'{uuid.uuid4().hex}.png'
        (SIGN_DIR / name).write_bytes(base64.b64decode(encoded))
        return f'signatures/{name}'
    except Exception:
        return None


def save_photo(file):
    if not file or not file.filename: return None
    ext = Path(file.filename).suffix.lower()
    if ext not in {'.jpg','.jpeg','.png','.webp'}: return None
    name = f'{uuid.uuid4().hex}{ext}'
    file.save(UPLOAD_DIR / name)
    return f'uploads/{name}'


@app.context_processor
def inject_globals():
    return {'now': datetime.now(), 'today': date.today()}

@app.route('/')
def dashboard():
    conn = get_db()
    stats = {
        'vehicles': conn.execute('SELECT COUNT(*) c FROM vehicles').fetchone()['c'],
        'available': conn.execute("SELECT COUNT(*) c FROM vehicles WHERE status='Disponibile'").fetchone()['c'],
        'active': conn.execute("SELECT COUNT(*) c FROM rentals WHERE status='In corso'").fetchone()['c'],
        'booked': conn.execute("SELECT COUNT(*) c FROM rentals WHERE status='Prenotato'").fetchone()['c'],
        'revenue': conn.execute("SELECT COALESCE(SUM(amount),0) s FROM payments").fetchone()['s'],
        'receivables': conn.execute("SELECT COALESCE(SUM(r.total),0)-COALESCE((SELECT SUM(amount) FROM payments),0) s FROM rentals r WHERE r.status!='Annullato'").fetchone()['s']
    }
    upcoming = rental_join(conn, "WHERE r.status IN ('Prenotato','In corso') ORDER BY r.start_date ASC LIMIT 8").fetchall()
    deadlines = conn.execute('''SELECT d.*,v.plate,v.brand,v.model FROM vehicle_deadlines d JOIN vehicles v ON v.id=d.vehicle_id
        WHERE d.completed=0 ORDER BY d.due_date LIMIT 8''').fetchall()
    conn.close()
    return render_template('dashboard.html', stats=stats, upcoming=upcoming, deadlines=deadlines)

@app.route('/vehicles')
def vehicles():
    conn=get_db(); rows=conn.execute('SELECT * FROM vehicles ORDER BY brand,model').fetchall(); conn.close()
    return render_template('vehicles.html', vehicles=rows)

@app.route('/vehicles/add', methods=['GET','POST'])
@app.route('/vehicles/<int:vehicle_id>/edit', methods=['GET','POST'])
def vehicle_form(vehicle_id=None):
    conn=get_db(); vehicle=conn.execute('SELECT * FROM vehicles WHERE id=?',(vehicle_id,)).fetchone() if vehicle_id else None
    if request.method=='POST':
        vals=(request.form['plate'].upper().strip(),request.form['brand'].strip(),request.form['model'].strip(),request.form.get('category','').strip(),float(request.form.get('daily_rate') or 0),request.form.get('status','Disponibile'),int(request.form.get('mileage') or 0),request.form.get('insurance_due') or None,request.form.get('inspection_due') or None,request.form.get('tax_due') or None,request.form.get('cargos_vehicle_type','').strip(),request.form.get('color','').strip(),1 if request.form.get('gps') else 0,1 if request.form.get('engine_block') else 0,request.form.get('notes','').strip())
        try:
            if vehicle_id:
                conn.execute('''UPDATE vehicles SET plate=?,brand=?,model=?,category=?,daily_rate=?,status=?,mileage=?,insurance_due=?,inspection_due=?,tax_due=?,cargos_vehicle_type=?,color=?,gps=?,engine_block=?,notes=? WHERE id=?''', vals+(vehicle_id,))
            else:
                conn.execute('''INSERT INTO vehicles(plate,brand,model,category,daily_rate,status,mileage,insurance_due,inspection_due,tax_due,cargos_vehicle_type,color,gps,engine_block,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)
            conn.commit(); flash('Veicolo salvato.','success'); conn.close(); return redirect(url_for('vehicles'))
        except sqlite3.IntegrityError: flash('Targa già presente.','danger')
    conn.close(); return render_template('vehicle_form.html', vehicle=vehicle)

@app.route('/vehicles/<int:vehicle_id>/delete',methods=['POST'])
def delete_vehicle(vehicle_id):
    conn=get_db(); used=conn.execute('SELECT COUNT(*) c FROM rentals WHERE vehicle_id=?',(vehicle_id,)).fetchone()['c']
    if used: flash('Impossibile eliminare: il veicolo è collegato a un noleggio.','danger')
    else: conn.execute('DELETE FROM vehicles WHERE id=?',(vehicle_id,)); conn.commit(); flash('Veicolo eliminato.','success')
    conn.close(); return redirect(url_for('vehicles'))

@app.route('/customers')
def customers():
    conn=get_db(); rows=conn.execute('SELECT * FROM customers ORDER BY last_name,first_name').fetchall(); conn.close(); return render_template('customers.html',customers=rows)

@app.route('/customers/add',methods=['GET','POST'])
@app.route('/customers/<int:customer_id>/edit',methods=['GET','POST'])
def customer_form(customer_id=None):
    conn=get_db(); customer=conn.execute('SELECT * FROM customers WHERE id=?',(customer_id,)).fetchone() if customer_id else None
    if request.method=='POST':
        vals=(request.form['first_name'].strip(),request.form['last_name'].strip(),request.form.get('phone','').strip(),request.form.get('email','').strip(),request.form.get('address','').strip(),request.form.get('birth_date') or None,request.form.get('birth_place_code','').strip(),request.form.get('citizenship_code','').strip(),request.form.get('residence_place_code','').strip(),request.form.get('document_type_code','').strip(),request.form.get('document_id','').strip(),request.form.get('document_issue_place_code','').strip(),request.form.get('license_number','').strip(),request.form.get('license_issue_place_code','').strip(),request.form.get('notes','').strip())
        if customer_id: conn.execute('''UPDATE customers SET first_name=?,last_name=?,phone=?,email=?,address=?,birth_date=?,birth_place_code=?,citizenship_code=?,residence_place_code=?,document_type_code=?,document_id=?,document_issue_place_code=?,license_number=?,license_issue_place_code=?,notes=? WHERE id=?''',vals+(customer_id,))
        else: conn.execute('''INSERT INTO customers(first_name,last_name,phone,email,address,birth_date,birth_place_code,citizenship_code,residence_place_code,document_type_code,document_id,document_issue_place_code,license_number,license_issue_place_code,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)
        conn.commit(); conn.close(); flash('Cliente salvato.','success'); return redirect(url_for('customers'))
    conn.close(); return render_template('customer_form.html',customer=customer)

@app.route('/rentals')
def rentals():
    conn=get_db(); rows=rental_join(conn,'ORDER BY r.start_date DESC').fetchall()
    paid={r['rental_id']:r['s'] for r in conn.execute('SELECT rental_id,SUM(amount) s FROM payments GROUP BY rental_id').fetchall()}
    conn.close(); return render_template('rentals.html',rentals=rows,paid=paid)

@app.route('/rentals/add',methods=['GET','POST'])
def add_rental():
    conn=get_db()
    if request.method=='POST':
        vehicle_id=int(request.form['vehicle_id']); customer_id=int(request.form['customer_id']); start=request.form['start_date']; end=request.form['end_date']
        if end<start: flash('La data di fine non può precedere la data di inizio.','danger')
        else:
            conflict=conn.execute("""SELECT COUNT(*) c FROM rentals WHERE vehicle_id=? AND status IN ('Prenotato','In corso') AND NOT(end_date<? OR start_date>?)""",(vehicle_id,start,end)).fetchone()['c']
            if conflict: flash('Veicolo già impegnato nel periodo selezionato.','danger')
            else:
                rate=conn.execute('SELECT daily_rate FROM vehicles WHERE id=?',(vehicle_id,)).fetchone()['daily_rate']; days=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).days+1
                total=float(request.form.get('total') or (rate*max(days,1))); status=request.form.get('status','Prenotato')
                cur=conn.execute('''INSERT INTO rentals(vehicle_id,customer_id,start_date,end_date,status,total,deposit,pickup_km,fuel_out,notes,created_at,pickup_time,return_time,cargos_payment_type,checkout_place_code,checkout_address,checkin_place_code,checkin_address,operator_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(vehicle_id,customer_id,start,end,status,total,float(request.form.get('deposit') or 0),int(request.form.get('pickup_km') or 0),request.form.get('fuel_out',''),request.form.get('notes','').strip(),datetime.now().isoformat(timespec='seconds'),request.form.get('pickup_time') or '09:00',request.form.get('return_time') or '09:00',request.form.get('cargos_payment_type','').strip(),request.form.get('checkout_place_code','').strip(),request.form.get('checkout_address','').strip(),request.form.get('checkin_place_code','').strip(),request.form.get('checkin_address','').strip(),request.form.get('operator_id','').strip()))
                if status=='In corso': conn.execute("UPDATE vehicles SET status='Noleggiato' WHERE id=?",(vehicle_id,))
                conn.commit(); rid=cur.lastrowid; conn.close(); flash('Noleggio creato. Ora puoi far firmare il contratto.','success'); return redirect(url_for('rental_detail',rental_id=rid))
    vehicles=conn.execute('SELECT * FROM vehicles ORDER BY brand,model').fetchall(); customers=conn.execute('SELECT * FROM customers ORDER BY last_name,first_name').fetchall(); conn.close()
    return render_template('rental_form.html',vehicles=vehicles,customers=customers)

@app.route('/rentals/<int:rental_id>')
def rental_detail(rental_id):
    conn=get_db(); rental=rental_join(conn,'WHERE r.id=?',(rental_id,)).fetchone(); payments=conn.execute('SELECT * FROM payments WHERE rental_id=? ORDER BY paid_at DESC',(rental_id,)).fetchall(); damages=conn.execute('SELECT * FROM damages WHERE rental_id=? ORDER BY damage_date DESC',(rental_id,)).fetchall(); cargos_history=conn.execute('SELECT * FROM cargos_submissions WHERE rental_id=? ORDER BY created_at DESC',(rental_id,)).fetchall(); conn.close()
    if not rental: return 'Noleggio non trovato',404
    return render_template('rental_detail.html',rental=rental,payments=payments,damages=damages,cargos_history=cargos_history,paid=sum(p['amount'] for p in payments))

@app.route('/rentals/<int:rental_id>/status',methods=['POST'])
def rental_status(rental_id):
    conn=get_db(); rental=conn.execute('SELECT * FROM rentals WHERE id=?',(rental_id,)).fetchone(); new=request.form['status']
    if rental:
        return_km=request.form.get('return_km'); fuel_in=request.form.get('fuel_in')
        conn.execute('UPDATE rentals SET status=?,return_km=COALESCE(?,return_km),fuel_in=COALESCE(?,fuel_in) WHERE id=?',(new,int(return_km) if return_km else None,fuel_in or None,rental_id))
        vstatus='Noleggiato' if new=='In corso' else 'Disponibile'; conn.execute('UPDATE vehicles SET status=? WHERE id=?',(vstatus,rental['vehicle_id']))
        if return_km: conn.execute('UPDATE vehicles SET mileage=? WHERE id=?',(int(return_km),rental['vehicle_id']))
        conn.commit(); flash('Stato aggiornato.','success')
    conn.close(); return redirect(url_for('rental_detail',rental_id=rental_id))

@app.route('/rentals/<int:rental_id>/sign',methods=['POST'])
def rental_sign(rental_id):
    path=save_signature(request.form.get('signature_data'))
    if path:
        conn=get_db(); conn.execute('UPDATE rentals SET signature=? WHERE id=?',(path,rental_id)); conn.commit(); conn.close(); flash('Firma salvata nel contratto.','success')
    else: flash('Firma non valida o vuota.','danger')
    return redirect(url_for('rental_detail',rental_id=rental_id))

@app.route('/rentals/<int:rental_id>/payment',methods=['POST'])
def add_payment(rental_id):
    amount=float(request.form.get('amount') or 0)
    if amount<=0: flash('Inserisci un importo valido.','danger')
    else:
        conn=get_db(); conn.execute('INSERT INTO payments(rental_id,amount,method,paid_at,reference,notes) VALUES(?,?,?,?,?,?)',(rental_id,amount,request.form.get('method','Contanti'),request.form.get('paid_at') or date.today().isoformat(),request.form.get('reference','').strip(),request.form.get('notes','').strip())); conn.commit(); conn.close(); flash('Pagamento registrato.','success')
    return redirect(url_for('rental_detail',rental_id=rental_id))

@app.route('/rentals/<int:rental_id>/contract')
def contract(rental_id):
    conn=get_db(); rental=rental_join(conn,'WHERE r.id=?',(rental_id,)).fetchone(); conn.close()
    if not rental: return 'Noleggio non trovato',404
    return render_template('contract.html',rental=rental)

@app.route('/rentals/<int:rental_id>/contract.pdf')
def contract_pdf(rental_id):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
    except ImportError:
        flash('Installa reportlab per generare il PDF.','danger'); return redirect(url_for('contract',rental_id=rental_id))
    conn=get_db(); r=rental_join(conn,'WHERE r.id=?',(rental_id,)).fetchone(); conn.close()
    if not r: return 'Noleggio non trovato',404
    buf=BytesIO(); doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm); styles=getSampleStyleSheet(); story=[]
    story += [Paragraph('CONTRATTO DI NOLEGGIO VEICOLO',styles['Title']),Spacer(1,8),Paragraph(f'Contratto n. {r["id"]} — generato il {date.today().strftime("%d/%m/%Y")}',styles['Normal']),Spacer(1,12)]
    data=[['Cliente',f'{r["first_name"]} {r["last_name"]}'],['Documento',r['document_id'] or '-'],['Patente',r['license_number'] or '-'],['Veicolo',f'{r["brand"]} {r["model"]} — {r["plate"]}'],['Periodo',f'{r["start_date"]} → {r["end_date"]}'],['Km consegna',str(r['pickup_km'] or 0)],['Totale',f'€ {r["total"]:.2f}'],['Cauzione',f'€ {r["deposit"]:.2f}']]
    t=Table(data,colWidths=[42*mm,118*mm]); t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.grey),('BACKGROUND',(0,0),(0,-1),colors.whitesmoke),('VALIGN',(0,0),(-1,-1),'TOP'),('PADDING',(0,0),(-1,-1),6)])); story += [t,Spacer(1,14)]
    story += [Paragraph('<b>Condizioni essenziali</b>',styles['Heading2']),Paragraph('Il cliente dichiara di ricevere il veicolo nello stato descritto, di possedere patente valida e di impegnarsi a rispettare le norme di circolazione. Carburante, danni, franchigie, multe e costi ulteriori restano disciplinati dalle condizioni concordate con il noleggiatore.',styles['BodyText']),Spacer(1,14)]
    if r['notes']: story += [Paragraph('<b>Note</b>',styles['Heading2']),Paragraph(r['notes'],styles['BodyText']),Spacer(1,14)]
    if r['signature']:
        p=STATIC_DIR/r['signature']
        if p.exists(): story += [Paragraph('<b>Firma cliente</b>',styles['Heading2']),Image(str(p),width=55*mm,height=25*mm)]
    else: story += [Paragraph('Firma cliente: _______________________________',styles['Normal'])]
    doc.build(story); buf.seek(0); return send_file(buf,mimetype='application/pdf',as_attachment=True,download_name=f'contratto_noleggio_{rental_id}.pdf')

@app.route('/calendar')
def calendar_view():
    try: y=int(request.args.get('year',date.today().year)); m=int(request.args.get('month',date.today().month))
    except: y,m=date.today().year,date.today().month
    cal=calendar.Calendar(firstweekday=0); weeks=cal.monthdatescalendar(y,m); start=weeks[0][0].isoformat(); end=weeks[-1][-1].isoformat(); conn=get_db(); rentals=rental_join(conn,"WHERE r.status!='Annullato' AND NOT(r.end_date<? OR r.start_date>?) ORDER BY r.start_date",(start,end)).fetchall(); conn.close()
    byday={}
    for r in rentals:
        d=datetime.fromisoformat(r['start_date']).date(); e=datetime.fromisoformat(r['end_date']).date()
        while d<=e:
            byday.setdefault(d.isoformat(),[]).append(r); d+=timedelta(days=1)
    prev=(date(y,m,1)-timedelta(days=1)); nxt=(date(y,m,calendar.monthrange(y,m)[1])+timedelta(days=1))
    return render_template('calendar.html',weeks=weeks,month=m,year=y,month_name=calendar.month_name[m],byday=byday,prev=prev,nxt=nxt)

@app.route('/deadlines',methods=['GET','POST'])
def deadlines():
    conn=get_db()
    if request.method=='POST':
        conn.execute('INSERT INTO vehicle_deadlines(vehicle_id,kind,due_date,amount,notes) VALUES(?,?,?,?,?)',(int(request.form['vehicle_id']),request.form['kind'],request.form['due_date'],float(request.form.get('amount') or 0),request.form.get('notes','').strip())); conn.commit(); flash('Scadenza aggiunta.','success')
    rows=conn.execute('''SELECT d.*,v.plate,v.brand,v.model FROM vehicle_deadlines d JOIN vehicles v ON v.id=d.vehicle_id ORDER BY d.completed,d.due_date''').fetchall(); vehicles=conn.execute('SELECT * FROM vehicles ORDER BY brand,model').fetchall(); conn.close(); return render_template('deadlines.html',deadlines=rows,vehicles=vehicles)

@app.route('/deadlines/<int:deadline_id>/toggle',methods=['POST'])
def deadline_toggle(deadline_id):
    conn=get_db(); conn.execute('UPDATE vehicle_deadlines SET completed=CASE completed WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(deadline_id,)); conn.commit(); conn.close(); return redirect(url_for('deadlines'))

@app.route('/damages',methods=['GET','POST'])
def damages():
    conn=get_db()
    if request.method=='POST':
        photo=save_photo(request.files.get('photo')); conn.execute('INSERT INTO damages(vehicle_id,rental_id,damage_date,description,cost,status,photo) VALUES(?,?,?,?,?,?,?)',(int(request.form['vehicle_id']),int(request.form['rental_id']) if request.form.get('rental_id') else None,request.form.get('damage_date') or date.today().isoformat(),request.form['description'].strip(),float(request.form.get('cost') or 0),request.form.get('status','Aperto'),photo)); conn.commit(); flash('Danno registrato.','success')
    rows=conn.execute('''SELECT d.*,v.plate,v.brand,v.model FROM damages d JOIN vehicles v ON v.id=d.vehicle_id ORDER BY d.damage_date DESC''').fetchall(); vehicles=conn.execute('SELECT * FROM vehicles ORDER BY brand,model').fetchall(); rents=rental_join(conn,'ORDER BY r.id DESC LIMIT 100').fetchall(); conn.close(); return render_template('damages.html',damages=rows,vehicles=vehicles,rentals=rents)

@app.route('/reports')
def reports():
    conn=get_db(); monthly=conn.execute("SELECT substr(paid_at,1,7) month,SUM(amount) total,COUNT(*) count FROM payments GROUP BY month ORDER BY month DESC LIMIT 12").fetchall(); methods=conn.execute('SELECT method,SUM(amount) total FROM payments GROUP BY method ORDER BY total DESC').fetchall(); totals=conn.execute("SELECT COALESCE(SUM(amount),0) paid FROM payments").fetchone(); outstanding=conn.execute("SELECT COALESCE(SUM(total),0) total FROM rentals WHERE status!='Annullato'").fetchone()['total']-totals['paid']; conn.close(); return render_template('reports.html',monthly=monthly,methods=methods,paid=totals['paid'],outstanding=outstanding)

# ------------------------- CaRGOS -------------------------
CARGOS_FIELDS = [
    ('CONTRATTO_ID',50),('CONTRATTO_DATA',16),('CONTRATTO_TIPOP',1),('CONTRATTO_CHECKOUT_DATA',16),
    ('CONTRATTO_CHECKOUT_LUOGO_COD',9),('CONTRATTO_CHECKOUT_INDIRIZZO',150),('CONTRATTO_CHECKIN_DATA',16),
    ('CONTRATTO_CHECKIN_LUOGO_COD',9),('CONTRATTO_CHECKIN_INDIRIZZO',150),('OPERATORE_ID',50),('AGENZIA_ID',30),
    ('AGENZIA_NOME',70),('AGENZIA_LUOGO_COD',9),('AGENZIA_INDIRIZZO',150),('AGENZIA_RECAPITO_TEL',20),
    ('VEICOLO_TIPO',1),('VEICOLO_MARCA',50),('VEICOLO_MODELLO',100),('VEICOLO_TARGA',15),('VEICOLO_COLORE',50),
    ('VEICOLO_GPS',1),('VEICOLO_BLOCCOM',1),('CONDUCENTE_CONTRAENTE_COGNOME',50),('CONDUCENTE_CONTRAENTE_NOME',30),
    ('CONDUCENTE_CONTRAENTE_NASCITA_DATA',10),('CONDUCENTE_CONTRAENTE_NASCITA_LUOGO_COD',9),
    ('CONDUCENTE_CONTRAENTE_CITTADINANZA_COD',9),('CONDUCENTE_CONTRAENTE_RESIDENZA_LUOGO_COD',9),
    ('CONDUCENTE_CONTRAENTE_RESIDENZA_INDIRIZZO',150),('CONDUCENTE_CONTRAENTE_DOCIDE_TIPO_COD',5),
    ('CONDUCENTE_CONTRAENTE_DOCIDE_NUMERO',20),('CONDUCENTE_CONTRAENTE_DOCIDE_LUOGORIL_COD',9),
    ('CONDUCENTE_CONTRAENTE_PATENTE_NUMERO',20),('CONDUCENTE_CONTRAENTE_PATENTE_LUOGORIL_COD',9),
    ('CONDUCENTE_CONTRAENTE_RECAPITO',20),('CONDUCENTE2_COGNOME',50),('CONDUCENTE2_NOME',30),
    ('CONDUCENTE2_NASCITA_DATA',10),('CONDUCENTE2_NASCITA_LUOGO_COD',9),('CONDUCENTE2_CITTADINANZA_COD',9),
    ('CONDUCENTE2_DOCIDE_TIPO_COD',5),('CONDUCENTE2_DOCIDE_NUMERO',20),('CONDUCENTE2_DOCIDE_LUOGORIL_COD',9),
    ('CONDUCENTE2_PATENTE_NUMERO',20),('CONDUCENTE2_PATENTE_LUOGORIL_COD',9),('CONDUCENTE2_RECAPITO',20)
]

def cargos_settings_row(conn):
    return conn.execute('SELECT * FROM cargos_settings WHERE id=1').fetchone()

def cargos_date(d, tm=None, with_time=False):
    if not d: return ''
    try:
        if 'T' in d:
            dt=datetime.fromisoformat(d)
        else:
            dt=datetime.fromisoformat(d + ('T'+(tm or '00:00') if with_time else ''))
        return dt.strftime('%d/%m/%Y %H:%M' if with_time else '%d/%m/%Y')
    except Exception:
        return ''

def cargos_clean(value):
    return '' if value is None else str(value).strip()

def cargos_build_record(r, cfg):
    values = {
        'CONTRATTO_ID': f'RF-{r["id"]}', 'CONTRATTO_DATA': cargos_date(r['created_at'], with_time=True),
        'CONTRATTO_TIPOP': r['cargos_payment_type'], 'CONTRATTO_CHECKOUT_DATA': cargos_date(r['start_date'],r['pickup_time'],True),
        'CONTRATTO_CHECKOUT_LUOGO_COD': r['checkout_place_code'], 'CONTRATTO_CHECKOUT_INDIRIZZO': r['checkout_address'],
        'CONTRATTO_CHECKIN_DATA': cargos_date(r['end_date'],r['return_time'],True), 'CONTRATTO_CHECKIN_LUOGO_COD': r['checkin_place_code'],
        'CONTRATTO_CHECKIN_INDIRIZZO': r['checkin_address'], 'OPERATORE_ID': r['operator_id'] or cfg['username'],
        'AGENZIA_ID': cfg['agency_id'],'AGENZIA_NOME':cfg['agency_name'],'AGENZIA_LUOGO_COD':cfg['agency_place_code'],
        'AGENZIA_INDIRIZZO':cfg['agency_address'],'AGENZIA_RECAPITO_TEL':cfg['agency_phone'], 'VEICOLO_TIPO':r['cargos_vehicle_type'],
        'VEICOLO_MARCA':r['brand'],'VEICOLO_MODELLO':r['model'],'VEICOLO_TARGA':r['plate'],'VEICOLO_COLORE':r['color'],
        'VEICOLO_GPS':'1' if r['gps'] else '0','VEICOLO_BLOCCOM':'1' if r['engine_block'] else '0',
        'CONDUCENTE_CONTRAENTE_COGNOME':r['last_name'],'CONDUCENTE_CONTRAENTE_NOME':r['first_name'],
        'CONDUCENTE_CONTRAENTE_NASCITA_DATA':cargos_date(r['birth_date']), 'CONDUCENTE_CONTRAENTE_NASCITA_LUOGO_COD':r['birth_place_code'],
        'CONDUCENTE_CONTRAENTE_CITTADINANZA_COD':r['citizenship_code'], 'CONDUCENTE_CONTRAENTE_RESIDENZA_LUOGO_COD':r['residence_place_code'],
        'CONDUCENTE_CONTRAENTE_RESIDENZA_INDIRIZZO':r['address'], 'CONDUCENTE_CONTRAENTE_DOCIDE_TIPO_COD':r['document_type_code'],
        'CONDUCENTE_CONTRAENTE_DOCIDE_NUMERO':r['document_id'], 'CONDUCENTE_CONTRAENTE_DOCIDE_LUOGORIL_COD':r['document_issue_place_code'],
        'CONDUCENTE_CONTRAENTE_PATENTE_NUMERO':r['license_number'], 'CONDUCENTE_CONTRAENTE_PATENTE_LUOGORIL_COD':r['license_issue_place_code'],
        'CONDUCENTE_CONTRAENTE_RECAPITO':r['phone'],
    }
    mandatory = ['CONTRATTO_TIPOP','CONTRATTO_CHECKOUT_LUOGO_COD','CONTRATTO_CHECKOUT_INDIRIZZO','CONTRATTO_CHECKIN_LUOGO_COD',
        'CONTRATTO_CHECKIN_INDIRIZZO','OPERATORE_ID','AGENZIA_ID','AGENZIA_NOME','AGENZIA_LUOGO_COD','AGENZIA_INDIRIZZO','AGENZIA_RECAPITO_TEL',
        'VEICOLO_TIPO','VEICOLO_MARCA','VEICOLO_MODELLO','VEICOLO_TARGA','CONDUCENTE_CONTRAENTE_COGNOME','CONDUCENTE_CONTRAENTE_NOME',
        'CONDUCENTE_CONTRAENTE_NASCITA_DATA','CONDUCENTE_CONTRAENTE_NASCITA_LUOGO_COD','CONDUCENTE_CONTRAENTE_CITTADINANZA_COD',
        'CONDUCENTE_CONTRAENTE_DOCIDE_TIPO_COD','CONDUCENTE_CONTRAENTE_DOCIDE_NUMERO','CONDUCENTE_CONTRAENTE_DOCIDE_LUOGORIL_COD',
        'CONDUCENTE_CONTRAENTE_PATENTE_NUMERO','CONDUCENTE_CONTRAENTE_PATENTE_LUOGORIL_COD']
    errors=[k for k in mandatory if not cargos_clean(values.get(k))]
    if bool(cargos_clean(values.get('CONDUCENTE_CONTRAENTE_RESIDENZA_LUOGO_COD'))) != bool(cargos_clean(values.get('CONDUCENTE_CONTRAENTE_RESIDENZA_INDIRIZZO'))):
        errors.append('RESIDENZA (codice luogo e indirizzo devono essere entrambi presenti)')
    record=''
    for name,width in CARGOS_FIELDS:
        value=cargos_clean(values.get(name,''))
        if len(value)>width: errors.append(f'{name} supera {width} caratteri')
        record += value[:width].ljust(width)
    if len(record)!=1505: errors.append(f'Lunghezza tracciato non valida: {len(record)}')
    return record, errors, values

def cargos_encrypt_aes(access_token, apikey):
    if not apikey or len(apikey) < 48: raise ValueError('APIKEY AES non valida: servono almeno 48 caratteri.')
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
    except ImportError as e: raise RuntimeError('Installa la dipendenza cryptography per usare CaRGOS.') from e
    key=apikey[:32].encode('utf-8'); iv=apikey[32:48].encode('utf-8')
    if len(key)!=32 or len(iv)!=16: raise ValueError('La APIKEY deve produrre una chiave AES di 32 byte e IV di 16 byte.')
    padder=padding.PKCS7(128).padder(); data=padder.update(access_token.encode('utf-8'))+padder.finalize()
    enc=Cipher(algorithms.AES(key),modes.CBC(iv)).encryptor(); out=enc.update(data)+enc.finalize()
    return base64.b64encode(out).decode('ascii')

def cargos_http_json(url, method='GET', headers=None, payload=None, basic=None):
    h={'Accept':'application/json'}; h.update(headers or {})
    if basic:
        token=base64.b64encode(f'{basic[0]}:{basic[1]}'.encode()).decode(); h['Authorization']='Basic '+token
    data=None
    if payload is not None:
        data=json.dumps(payload).encode('utf-8'); h['Content-Type']='application/json'
    req=urllib.request.Request(url,data=data,headers=h,method=method)
    try:
        with urllib.request.urlopen(req,timeout=20,context=ssl.create_default_context()) as resp:
            raw=resp.read().decode('utf-8','replace'); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body=e.read().decode('utf-8','replace'); raise RuntimeError(f'HTTP {e.code}: {body[:500]}')
    except urllib.error.URLError as e: raise RuntimeError(f'Connessione CaRGOS non riuscita: {e.reason}')

def cargos_get_access(cfg):
    if not cfg['username'] or not cfg['password'] or not cfg['apikey']: raise ValueError('Configura username, password e APIKEY nella sezione CaRGOS.')
    base=(cfg['api_base'] or 'https://cargos.poliziadistato.it/CARGOS_API/').rstrip('/')+'/'
    result=cargos_http_json(base+'api/Token',basic=(cfg['username'],cfg['password']))
    token = result.get('access_token') or ((result.get('token') or {}).get('access_token') if isinstance(result,dict) else None)
    if not token: raise RuntimeError('Il servizio non ha restituito access_token: '+json.dumps(result,ensure_ascii=False)[:500])
    return base, cargos_encrypt_aes(token,cfg['apikey'])

def cargos_remote(rental_id, action):
    conn=get_db(); r=rental_join(conn,'WHERE r.id=?',(rental_id,)).fetchone(); cfg=cargos_settings_row(conn)
    if not r: conn.close(); raise ValueError('Noleggio non trovato.')
    record, errors, _=cargos_build_record(r,cfg)
    if errors: conn.close(); raise ValueError('Completa i campi CaRGOS: '+', '.join(errors))
    base, encrypted=cargos_get_access(cfg); endpoint='api/Check' if action=='CHECK' else 'api/Send'
    result=cargos_http_json(base+endpoint,method='POST',headers={'Authorization':'Bearer '+encrypted,'Organization':cfg['username']},payload=[record])
    result_text=json.dumps(result,ensure_ascii=False); tx=''; err=''
    def walk(obj):
        nonlocal tx,err
        if isinstance(obj,dict):
            for k,v in obj.items():
                lk=k.lower()
                if lk in ('transactionid','transaction_id') and v: tx=str(v)
                if lk in ('error_description','errordescription') and v and not err: err=str(v)
                walk(v)
        elif isinstance(obj,list):
            for x in obj: walk(x)
    walk(result)
    status='OK' if (action=='CHECK' and not err) or (action=='SEND' and tx and not err) else ('ERRORE' if err else 'RISPOSTA')
    conn.execute('INSERT INTO cargos_submissions(rental_id,action,status,transaction_id,error_message,record_text,response_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (rental_id,action,status,tx,err,record,result_text,datetime.now().isoformat(timespec='seconds')))
    conn.commit(); conn.close(); return result,tx,err

@app.route('/cargos',methods=['GET','POST'])
def cargos_page():
    conn=get_db()
    if request.method=='POST':
        old=cargos_settings_row(conn); password=request.form.get('password','') or (old['password'] or ''); apikey=request.form.get('apikey','') or (old['apikey'] or '')
        conn.execute('''UPDATE cargos_settings SET username=?,password=?,apikey=?,agency_id=?,agency_name=?,agency_place_code=?,agency_address=?,agency_phone=?,api_base=? WHERE id=1''',(
            request.form.get('username','').strip(),password,apikey,request.form.get('agency_id','').strip(),request.form.get('agency_name','').strip(),
            request.form.get('agency_place_code','').strip(),request.form.get('agency_address','').strip(),request.form.get('agency_phone','').strip(),
            request.form.get('api_base','').strip() or 'https://cargos.poliziadistato.it/CARGOS_API/'))
        conn.commit(); flash('Configurazione CaRGOS salvata nel database locale.','success')
    cfg=cargos_settings_row(conn); rows=rental_join(conn,"WHERE r.status!='Annullato' ORDER BY r.created_at DESC LIMIT 100").fetchall()
    history=conn.execute('''SELECT s.*,r.start_date,v.plate,c.first_name,c.last_name FROM cargos_submissions s JOIN rentals r ON r.id=s.rental_id JOIN vehicles v ON v.id=r.vehicle_id JOIN customers c ON c.id=r.customer_id ORDER BY s.created_at DESC LIMIT 30''').fetchall()
    prepared=[]
    for r in rows:
        _,errors,_=cargos_build_record(r,cfg); prepared.append((r,errors))
    conn.close(); return render_template('cargos.html',cfg=cfg,prepared=prepared,history=history)

@app.route('/rentals/<int:rental_id>/cargos/preview')
def cargos_preview(rental_id):
    conn=get_db(); r=rental_join(conn,'WHERE r.id=?',(rental_id,)).fetchone(); cfg=cargos_settings_row(conn); conn.close()
    if not r: return 'Noleggio non trovato',404
    record,errors,values=cargos_build_record(r,cfg); return render_template('cargos_preview.html',rental=r,record=record,errors=errors,values=values,fields=CARGOS_FIELDS)

@app.route('/rentals/<int:rental_id>/cargos/check',methods=['POST'])
def cargos_check(rental_id):
    try:
        _,tx,err=cargos_remote(rental_id,'CHECK'); flash('Controllo CaRGOS completato.' if not err else 'CaRGOS ha segnalato: '+err,'success' if not err else 'danger')
    except Exception as e: flash(str(e),'danger')
    return redirect(url_for('rental_detail',rental_id=rental_id))

@app.route('/rentals/<int:rental_id>/cargos/send',methods=['POST'])
def cargos_send(rental_id):
    try:
        _,tx,err=cargos_remote(rental_id,'SEND')
        if tx: flash('Contratto inviato a CaRGOS. Attestazione/transaction ID: '+tx,'success')
        elif err: flash('Invio CaRGOS rifiutato: '+err,'danger')
        else: flash('Risposta CaRGOS ricevuta; verifica lo storico.','success')
    except Exception as e: flash(str(e),'danger')
    return redirect(url_for('rental_detail',rental_id=rental_id))

def configure_android_storage(path):
    global BASE_DIR, DATA_DIR, DB_PATH, STATIC_DIR, UPLOAD_DIR, SIGN_DIR
    DATA_DIR = Path(path)
    BASE_DIR = DATA_DIR
    DB_PATH = DATA_DIR / 'autonoleggio.db'
    STATIC_DIR = DATA_DIR / 'static'
    UPLOAD_DIR = STATIC_DIR / 'uploads'
    SIGN_DIR = STATIC_DIR / 'signatures'
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SIGN_DIR.mkdir(parents=True, exist_ok=True)
    # Copia gli asset web distribuiti con l'app nella memoria privata Android.
    source_static = SOURCE_DIR / 'static'
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for src in source_static.iterdir():
        if src.is_file():
            (STATIC_DIR / src.name).write_bytes(src.read_bytes())
    app.template_folder = str(SOURCE_DIR / 'templates')
    app.static_folder = str(STATIC_DIR)


def start_android(data_path):
    configure_android_storage(data_path)
    init_db()
    thread = threading.Thread(
        target=lambda: app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    thread.start()
    return True


if __name__=='__main__':
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SIGN_DIR.mkdir(parents=True, exist_ok=True)
    init_db(); app.run(host='0.0.0.0',port=5000,debug=True)
