import { LensIcon } from "./Brand";
import { CalendarIcon, ChevronRightIcon, ReceiptIcon, ActivityIcon, UsersIcon } from "./Icons";

export const SUGGESTIONS = [
  { text: "How many patients are registered?", Icon: UsersIcon },
  { text: "What is the total amount of paid bills?", Icon: ReceiptIcon },
  { text: "How many appointments does each doctor have, including doctors with no appointments?", Icon: CalendarIcon },
  { text: "Show the number of treatments by type.", Icon: ActivityIcon },
];

/** Clicking a suggestion only fills the composer; the user still sends it. */
export function HomeView({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="home">
      <LensIcon size={56} handle="var(--color-accent)" className="home__lens" />
      <h1 className="home__title">What would you like to know?</h1>
      <p className="home__subtitle">Ask about patients, doctors, appointments, treatments or billing.</p>
      <ul className="suggestions" aria-label="Suggested questions">
        {SUGGESTIONS.map(({ text, Icon }) => (
          <li key={text}>
            <button type="button" className="suggestion" onClick={() => onPick(text)}>
              <span className="suggestion__icon"><Icon size={20} /></span>
              <span className="suggestion__text">{text}</span>
              <ChevronRightIcon size={18} className="suggestion__chevron" />
            </button>
          </li>
        ))}
      </ul>
      <p className="home__note">
        Follow-up questions use the context of their conversation. That memory is temporary: it expires after a
        period of inactivity and is lost when the server restarts.
      </p>
    </div>
  );
}
