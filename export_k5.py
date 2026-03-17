"""
Выгрузка данных из chemico_db по шаблону К5_2521.xlsx.
"""
import os, sys, django, tempfile
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meetrec.settings')
sys.path.insert(0, '/app')
django.setup()

import pandas as pd
from sqlalchemy import create_engine, text
from chemico_agent.agent import CHEMICO_DB_URL

engine = create_engine(CHEMICO_DB_URL)

# Алиасы ≤ 63 chars (postgres ограничение); переименуем потом
SQL = """
WITH
max_product AS (
    SELECT p.name AS product_name
    FROM deal_profit_calculator_productunit pu
    JOIN deal_profit_calculator_product p ON p.id = pu.product_id
    WHERE pu.unit_price IS NOT NULL
    ORDER BY pu.unit_price * COALESCE(pu.actual_amount, pu.planned_amount, 1) DESC
    LIMIT 1
),
rates AS (
    SELECT DISTINCT ON (c2.name)
        c2.name  AS cur_name,
        cv.coefficient,
        cv.updated_at
    FROM deal_profit_calculator_currencyunitconverter cv
    JOIN deal_profit_calculator_currencyunit c1 ON c1.id = cv.currency_unit_1_id AND c1.name = 'RUB'
    JOIN deal_profit_calculator_currencyunit c2 ON c2.id = cv.currency_unit_2_id
    WHERE cv.coefficient > 0
    ORDER BY c2.name, cv.updated_at DESC
)
SELECT
    ROW_NUMBER() OVER (ORDER BY d.registration_number, pu.id) AS n_pp,
    d.registration_number           AS n_sdelki,
    TO_CHAR(d.created_at AT TIME ZONE 'UTC', 'DD.MM.YYYY')   AS dat_reg_sdelki,
    COALESCE(legal_co.name, 'ООО ТГ Фрейм')                  AS yur_lico,
    COALESCE(legal_co.name, 'ООО ТГ Фрейм')                  AS yur_lico_bank,
    CASE pu.type
        WHEN 'PURCHASED' THEN 'Закупка'
        WHEN 'SOLD'      THEN 'Продажа'
        ELSE pu.type END                                       AS tip_op,
    CASE pu.type
        WHEN 'PURCHASED' THEN 'Закупка товара'
        WHEN 'SOLD'      THEN 'Продажа товара'
        ELSE pu.type END                                       AS rassh_op,
    TO_CHAR(COALESCE(pu.actual_date, pu.planed_date) AT TIME ZONE 'UTC', 'DD.MM.YYYY') AS dat_op,
    EXTRACT(MONTH FROM COALESCE(pu.actual_date, pu.planed_date))::int AS mes_op,
    EXTRACT(YEAR  FROM COALESCE(pu.actual_date, pu.planed_date))::int AS god_op,
    COALESCE(pg.name, 'Товар')                                AS byudzhet,
    ROUND(pu.unit_price * COALESCE(pu.actual_amount, pu.planned_amount, 1)
          * COALESCE(r.coefficient, 1), 2)                    AS summa_s_nds,
    ROUND(pu.unit_price * COALESCE(pu.actual_amount, pu.planned_amount, 1)
          * COALESCE(r.coefficient, 1) * 20 / 120, 2)         AS summa_nds,
    ROUND(pu.unit_price * COALESCE(pu.actual_amount, pu.planned_amount, 1)
          * COALESCE(r.coefficient, 1) / 1.2, 2)              AS summa_bez_nds,
    ROUND(pu.unit_price * COALESCE(pu.actual_amount, pu.planned_amount, 1), 2) AS summa_val,
    cu.name                                                   AS valyuta,
    NULL::text                                                AS komm_platezh,
    pu.contract_condition                                     AS osnov_platezh,
    COALESCE(r.coefficient, 1)                                AS kurs,
    r.updated_at::date                                        AS dat_kursa,
    COALESCE(pu.actual_amount, pu.planned_amount, 1)          AS kolvo,
    mu.name                                                   AS ed_izm,
    NULL::text                                                AS dokument,
    NULL::text                                                AS n_dokumenta,
    NULL::text                                                AS dat_dokumenta,
    NULL::text                                                AS pechat,
    pu.contract_condition                                     AS komm_dok_nazn,
    NULL::text                                                AS sch_f,
    NULL::text                                                AS n_dogovora,
    TO_CHAR(pu.contract_date AT TIME ZONE 'UTC', 'DD.MM.YYYY') AS dat_dogovora,
    NULL::text                                                AS unk,
    ship_co.name                                              AS kontragent,
    (SELECT product_name FROM max_product)                    AS tovar,
    CASE pu.type WHEN 'PURCHASED' THEN ship_co.name ELSE NULL END AS postavshik,
    NULL::text                                                AS dog_s_post,
    NULL::text                                                AS osnov_dog_post,
    CASE pu.type WHEN 'SOLD'      THEN ship_co.name ELSE NULL END AS pokupatl,
    NULL::text                                                AS dog_s_pok,
    NULL::text                                                AS osnov_dog_pok,
    mfg_co.name                                               AS proizvoditl,
    CASE pu.type WHEN 'PURCHASED' THEN ship_co.name
                 ELSE COALESCE(legal_co.name,'ООО ТГ Фрейм') END AS gruz_otprav,
    CASE pu.type WHEN 'SOLD'      THEN ship_co.name
                 ELSE COALESCE(legal_co.name,'ООО ТГ Фрейм') END AS gruz_poluch,
    NULL::text                                                AS plat_agent,
    CASE WHEN pu.actual_date IS NOT NULL OR pu.actual_amount IS NOT NULL
         THEN 'факт' ELSE 'план' END                          AS plan_fakt,
    CASE WHEN pu.actual_date IS NOT NULL OR pu.actual_amount IS NOT NULL
         THEN 'факт' ELSE 'план' END                          AS plan_fakt2,
    NULL::text                                                AS period_zakr,
    NULL::text                                                AS status_op,
    CONCAT(COALESCE(pm.last_name,''), ' ', COALESCE(pm.first_name,'')) AS nop_mp,
    CONCAT(
        COALESCE(css_u.last_name,''), ' ', COALESCE(css_u.first_name,''),
        CASE WHEN hsd_u.id IS NOT NULL
             THEN CONCAT(', ', COALESCE(hsd_u.last_name,''), ' ', COALESCE(hsd_u.first_name,''))
             ELSE '' END
    )                                                         AS srk,
    NULL::text                                                AS primechanie,
    'На день первичных документов'                            AS post_kurs,
    'На день первичных документов'                            AS pok_kurs,
    NULL::text                                                AS komissioner,
    NULL::numeric                                             AS kl_tr_ed,
    TO_CHAR(pu.planed_date AT TIME ZONE 'UTC', 'DD.MM.YYYY')  AS dat_postavki,
    CASE WHEN pu.actual_date IS NOT NULL OR pu.actual_amount IS NOT NULL
         THEN 'факт' ELSE 'план' END                          AS status_zapis

FROM deal_profit_calculator_productunit pu
LEFT JOIN deal_profit_calculator_product p          ON p.id = pu.product_id
LEFT JOIN deal_profit_calculator_productgroup pg    ON pg.id = p.group_id
LEFT JOIN deal_profit_calculator_currencyunit cu    ON cu.id = pu.currency_unit_id
LEFT JOIN deal_profit_calculator_measurementunit mu ON mu.id = pu.measurement_unit_id
LEFT JOIN deal_profit_calculator_manufacturer mfg   ON mfg.id = pu.manufacturer_company_id
LEFT JOIN deal_company mfg_co                       ON mfg_co.draggable_ptr_id = mfg.id
LEFT JOIN deal_profit_calculator_providerunit prov  ON prov.id = pu.provider_unit_id
LEFT JOIN deal_company ship_co                      ON ship_co.draggable_ptr_id = prov.shipper_company_id
LEFT JOIN deal_deal d                               ON d.draggable_ptr_id = pu.deal_id
LEFT JOIN deal_profit_calculator_dealprofitcalculate dpc ON dpc.id = d.deal_profit_calculator_id
LEFT JOIN deal_company legal_co                     ON legal_co.draggable_ptr_id = dpc.company_id
LEFT JOIN auth_user pm                              ON pm.id = dpc.pm_id
LEFT JOIN auth_user css_u                           ON css_u.id = dpc.css_id
LEFT JOIN auth_user hsd_u                           ON hsd_u.id = dpc.hsd_id
LEFT JOIN rates r                                   ON r.cur_name = cu.name

WHERE d.registration_number ~ '^К'
ORDER BY d.registration_number, pu.id
"""

# Финальные имена колонок К5
COL_RENAME = {
    'n_pp':         '№ п/п',
    'n_sdelki':     '№ сделки',
    'dat_reg_sdelki':'дата регистрации сделки',
    'yur_lico':     'юр. лицо',
    'yur_lico_bank':'юр.лицо (банк)',
    'tip_op':       'тип операции',
    'rassh_op':     'расшифровка операции',
    'dat_op':       'дата операции',
    'mes_op':       'месяц операции',
    'god_op':       'год операции',
    'byudzhet':     'бюджет',
    'summa_s_nds':  'сумма (в т.ч. НДС), руб.',
    'summa_nds':    'сумма НДС, руб.',
    'summa_bez_nds':'сумма без НДС, руб.',
    'summa_val':    'сумма в валюте',
    'valyuta':      'валюта',
    'komm_platezh': 'комментарии к платежу',
    'osnov_platezh':'основание платежа',
    'kurs':         'курс',
    'dat_kursa':    'дата курса',
    'kolvo':        'кол-во',
    'ed_izm':       'единица измерения',
    'dokument':     'документ',
    'n_dokumenta':  '№ документа',
    'dat_dokumenta':'дата документа',
    'pechat':       'печать',
    'komm_dok_nazn':'комментарий к документу/назначение платежа',
    'sch_f':        'сч-ф',
    'n_dogovora':   '№ договора',
    'dat_dogovora': 'дата договора',
    'unk':          'УНК',
    'kontragent':   'контрагент',
    'tovar':        'товар',
    'postavshik':   'поставщик',
    'dog_s_post':   'договор с поставщиком',
    'osnov_dog_post':'Основание к договору с поставщиком',
    'pokupatl':     'покупатель',
    'dog_s_pok':    'договор с покупателем',
    'osnov_dog_pok':'Основание к договору с покупателем',
    'proizvoditl':  'производитель',
    'gruz_otprav':  'грузоотправитель',
    'gruz_poluch':  'грузополучатель',
    'plat_agent':   'Платёжный агент',
    'plan_fakt':    'план/факт',
    'plan_fakt2':   'план//факт',
    'period_zakr':  'период закрытия',
    'status_op':    'статус',
    'nop_mp':       'НОП/МП',
    'srk':          'СРК',
    'primechanie':  'примечание',
    'post_kurs':    'поставщик (курс на день)',
    'pok_kurs':     'покупатель (курс на день)',
    'komissioner':  'Комиссионер',
    'kl_tr_ed':     'кл-во тр ед',
    'dat_postavki': 'дата поставки',
    'status_zapis': 'Статус',
}

print("Running SQL...")
with engine.connect() as conn:
    df = pd.read_sql(text(SQL), conn)

df = df.rename(columns=COL_RENAME)
print(f"Got {len(df)} rows, {len(df.columns)} cols")

# Показываем статистику заполненности
print("\nЗаполненность колонок:")
for col in df.columns:
    filled = df[col].notna().sum()
    pct = int(filled / len(df) * 100)
    bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
    print(f"  {bar} {pct:3d}%  {col}")

# Сохраняем Excel
out = '/tmp/k5_export.xlsx'
with pd.ExcelWriter(out, engine='openpyxl') as w:
    df.to_excel(w, index=False, sheet_name='К5')
    ws = w.sheets['К5']
    for col_cells in ws.columns:
        ml = max(len(str(cell.value or '')) for cell in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = min(ml + 3, 50)
print(f"\nSaved: {out}")
