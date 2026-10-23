// Data catalog: the tables a user can ask about and, per table, its columns, keys and relationships.
// Everything shown comes from GET /catalog/tables and GET /catalog/tables/{name}: structure from
// database/schema.sql, descriptions from the data catalog (null stays "No description available").
// No row counts, sample rows or freshness dates exist in the API, so none are shown. Browsing the
// catalog never sends a chat message, runs the model or queries hospital data.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Failure, Loading } from "../components/PageStatus";
import {
  ActivityIcon, CalendarIcon, ChartIcon, ChevronRightIcon, DatabaseIcon, MessageIcon, ReceiptIcon, UsersIcon,
} from "../components/Icons";
import { getCatalog, getTable } from "../services/adminApi";
import type { ColumnInfo, KeyRole, Loaded, TableDetail, TableSummary, CatalogResponse } from "../services/adminTypes";

const TABLE_ICONS: Record<string, (p: { size?: number }) => JSX.Element> = {
  patients: UsersIcon, doctors: ActivityIcon, appointments: CalendarIcon, treatments: ChartIcon, billing: ReceiptIcon,
};

const ROLE_LABELS: Record<KeyRole, string> = {
  primary_key: "Primary key",
  unique: "Unique",
  unique_part: "Part of unique key",
  foreign_key: "Foreign key",
  indexed: "Indexed",
};

function Unavailable({ children = "No description available" }: { children?: ReactNode }) {
  return <span className="catalog__missing">{children}</span>;
}

interface Props {
  /** The table whose detail is open (kept by the workspace, so it survives a visit to the chat). */
  selected: string | null;
  onSelect: (table: string | null) => void;
  /** Open a new conversation, optionally with a draft; nothing is sent. */
  onAsk: (draft?: string) => void;
}

export function CatalogPage({ selected, onSelect, onAsk }: Props) {
  return selected ? (
    <TableView key={selected} name={selected} onSelect={onSelect} onAsk={onAsk} />
  ) : (
    <TableList onSelect={onSelect} onAsk={onAsk} />
  );
}

function useLoad<T>(load: () => Promise<Loaded<T>>) {
  const [state, setState] = useState<Loaded<T> | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState(null);
    void load().then((result) => {
      if (!cancelled) setState(result);
    });
    return () => {
      cancelled = true;
    };
    // `load` is stable per page; `attempt` re-runs it for "Try again".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, retry };
}

function TableList({ onSelect, onAsk }: Pick<Props, "onSelect" | "onAsk">) {
  const { state, retry } = useLoad<CatalogResponse>(getCatalog);
  const [filter, setFilter] = useState("");
  const tables = state?.ok ? state.data.tables : [];
  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return tables.filter((t) => {
      const text = [t.name, t.display_name, t.description ?? "", t.grain ?? ""].join(" ").toLowerCase();
      return words.every((w) => text.includes(w));
    });
  }, [tables, filter]);

  return (
    <div className="page">
      <p className="page__eyebrow">Data explorer</p>
      <h1 className="page__title">Know your hospital data</h1>
      <p className="page__subtitle">The tables MedLens can answer questions about. Open a table to see its columns, keys and relationships.</p>

      {state === null && <Loading label="Loading the data catalog…" />}
      {state && !state.ok && (
        <Failure title="The data catalog is unavailable" message={state.message} onRetry={retry} />
      )}
      {state?.ok && (
        <>
          <p className="catalog__strip">
            <DatabaseIcon size={16} /> {tables.length} {tables.length === 1 ? "table" : "tables"} available · Read-only access
          </p>
          {tables.length === 0 ? (
            <p className="page__empty">The catalog lists no tables.</p>
          ) : (
            <>
              <div className="catalog__search">
                <label htmlFor="catalog-filter" className="field__label">Filter tables</label>
                <input id="catalog-filter" className="field__input" type="search" value={filter}
                  placeholder="Name or description, e.g. billing" onChange={(e) => setFilter(e.target.value)} />
              </div>
              {shown.length === 0 ? (
                <p className="page__empty" role="status">No table matches “{filter.trim()}”.</p>
              ) : (
                <ul className="catalog__grid" aria-label="Tables">
                  {shown.map((t) => <TableCard key={t.name} table={t} onOpen={() => onSelect(t.name)} />)}
                </ul>
              )}
            </>
          )}
          <div className="catalog__cta">
            <p>Ready to explore? Ask a question in plain English.</p>
            <button type="button" className="btn btn--primary" onClick={() => onAsk()}>
              <MessageIcon size={16} /> Ask a question
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function TableCard({ table, onOpen }: { table: TableSummary; onOpen: () => void }) {
  const Icon = TABLE_ICONS[table.name] ?? DatabaseIcon;
  return (
    <li>
      <button type="button" className="catalog-card" onClick={onOpen}>
        <span className="catalog-card__icon" aria-hidden="true"><Icon size={18} /></span>
        <span className="catalog-card__name">{table.display_name}</span>
        <span className="catalog-card__code">{table.name}</span>
        <span className="catalog-card__text">{table.description ?? <Unavailable />}</span>
        <span className="catalog-card__meta">
          {table.grain ? <span>{table.grain}</span> : <Unavailable>Grain not documented</Unavailable>}
          <span>{table.column_count} {table.column_count === 1 ? "column" : "columns"}</span>
        </span>
        <span className="catalog-card__link">View columns and keys <ChevronRightIcon size={14} /></span>
      </button>
    </li>
  );
}

function TableView({ name, onSelect, onAsk }: { name: string } & Pick<Props, "onSelect" | "onAsk">) {
  const load = useCallback(() => getTable(name), [name]);
  const { state, retry } = useLoad<TableDetail>(load);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (state?.ok) heading.current?.focus();
  }, [state]);

  const back = (
    <button type="button" className="btn btn--outline btn--small" onClick={() => onSelect(null)}>
      ← Data catalog
    </button>
  );
  if (state === null) return <div className="page">{back}<Loading label={`Loading ${name}…`} /></div>;
  if (!state.ok) {
    return (
      <div className="page">
        {back}
        {state.kind === "not_found" ? (
          <Failure title={`There is no table called “${name}”`} message="It may have been renamed. Go back to the catalog to see the available tables." />
        ) : (
          <Failure title="This table's details are unavailable" message={state.message} onRetry={retry} />
        )}
      </div>
    );
  }
  const t = state.data;
  const tableLink = (table: string) => (
    <button type="button" className="link-btn" onClick={() => onSelect(table)}>{table}</button>
  );

  return (
    <div className="page">
      {back}
      <h1 className="page__title page__title--detail" tabIndex={-1} ref={heading}>{t.display_name}</h1>
      <p className="catalog__code-line"><code>{t.name}</code></p>
      <p className="page__subtitle">{t.description ?? <Unavailable />}</p>
      <div className="catalog__facts">
        <span className="meta-pill">Grain: {t.grain ?? "not documented"}</span>
        <span className="meta-pill">{t.column_count} {t.column_count === 1 ? "column" : "columns"}</span>
        <span className="meta-pill">Primary key: {t.primary_key.length ? t.primary_key.join(", ") : "none"}</span>
      </div>

      <section className="catalog__section" aria-labelledby="columns-h">
        <h2 id="columns-h" className="section-title">Columns</h2>
        <div className="table-scroll table-scroll--tall">
          <table className="result-table catalog-table">
            <thead>
              <tr><th scope="col">Column</th><th scope="col">Description</th><th scope="col">Type</th>
                <th scope="col">Nullable</th><th scope="col">Keys</th></tr>
            </thead>
            <tbody>
              {t.columns.map((c) => <ColumnRow key={c.name} column={c} tableLink={tableLink} />)}
            </tbody>
          </table>
        </div>
      </section>

      <section className="catalog__section" aria-labelledby="rel-h">
        <h2 id="rel-h" className="section-title">Relationships</h2>
        <div className="catalog__columns">
          <div className="catalog__box">
            <h3 className="box-title">Foreign keys (this table → other tables)</h3>
            {t.foreign_keys.length === 0 ? <p className="muted">None. This table does not reference another table.</p> : (
              <ul className="catalog__list">
                {t.foreign_keys.map((fk) => (
                  <li key={`${fk.name}-${fk.columns.join()}`}>
                    <code>{fk.columns.join(", ")}</code> → {tableLink(fk.references_table)}.<code>{fk.references_columns.join(", ")}</code>
                    {fk.name && <span className="catalog__key-name">{fk.name}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="catalog__box">
            <h3 className="box-title">Referenced by (other tables → this table)</h3>
            {t.referenced_by.length === 0 ? <p className="muted">No other table references this one.</p> : (
              <ul className="catalog__list">
                {t.referenced_by.map((r) => (
                  <li key={`${r.from_table}-${r.from_columns.join()}`}>
                    {tableLink(r.from_table)}.<code>{r.from_columns.join(", ")}</code> → <code>{t.name}.{r.to_columns.join(", ")}</code>
                    {r.name && <span className="catalog__key-name">{r.name}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <section className="catalog__section" aria-labelledby="keys-h">
        <h2 id="keys-h" className="section-title">Unique constraints and indexes</h2>
        <div className="catalog__columns">
          <div className="catalog__box">
            <h3 className="box-title">Unique constraints</h3>
            <p className="box-note">No two rows may share these values (NULLs aside). The primary key is listed above.</p>
            {t.unique_constraints.length === 0 ? <p className="muted">None besides the primary key.</p> : (
              <ul className="catalog__list">
                {t.unique_constraints.map((k) => (
                  <li key={k.columns.join()}><code>{k.columns.join(", ")}</code>{k.name && <span className="catalog__key-name">{k.name}</span>}</li>
                ))}
              </ul>
            )}
          </div>
          <div className="catalog__box">
            <h3 className="box-title">Indexes (non-unique)</h3>
            <p className="box-note">Speed up lookups only; they do not restrict values.</p>
            {t.indexes.length === 0 ? <p className="muted">None.</p> : (
              <ul className="catalog__list">
                {t.indexes.map((k) => (
                  <li key={k.columns.join()}><code>{k.columns.join(", ")}</code>{k.name && <span className="catalog__key-name">{k.name}</span>}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <div className="catalog__cta">
        <p>Questions are answered with read-only queries; nothing here runs a query.</p>
        <button type="button" className="btn btn--primary" onClick={() => onAsk(`About ${t.display_name.toLowerCase()}: `)}>
          <MessageIcon size={16} /> Ask about {t.display_name.toLowerCase()}
        </button>
      </div>
    </div>
  );
}

function ColumnRow({ column: c, tableLink }: { column: ColumnInfo; tableLink: (t: string) => JSX.Element }) {
  return (
    <tr>
      <th scope="row"><code>{c.name}</code></th>
      <td className="catalog-table__desc">{c.description ?? <Unavailable />}</td>
      <td>
        <code>{c.data_type}</code>
        {c.allowed_values && (
          <span className="catalog__values" aria-label={`Allowed values: ${c.allowed_values.join(", ")}`}>
            {c.allowed_values.map((v) => <span key={v} className="value-chip">{v}</span>)}
          </span>
        )}
      </td>
      <td>{c.nullable ? "Yes" : "No"}</td>
      <td>
        {c.key_roles.length === 0 ? <span className="muted">—</span> : (
          <span className="catalog__roles">
            {c.key_roles.map((r) => <span key={r} className={`role-chip role-chip--${r}`}>{ROLE_LABELS[r]}</span>)}
            {c.references && <span className="catalog__ref">→ {tableLink(c.references.table)}.<code>{c.references.column}</code></span>}
          </span>
        )}
      </td>
    </tr>
  );
}
