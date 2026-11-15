import { Link } from "react-router-dom";
import { ArrowRight, Bike, Briefcase, CheckCircle2, Receipt, Wrench, type LucideIcon } from "lucide-react";
import { Reveal } from "../ui/Reveal";

const SERVICES: { icon: LucideIcon; eyebrow: string; title: string; points: string[]; cta: string; to: string }[] = [
  {
    icon: Receipt, eyebrow: "Bill payments", title: "Pay every bill in seconds",
    points: ["Airtime & data on MTN, Airtel, Glo and T2", "Electricity tokens for all 12 DisCos", "DStv, GOtv & StarTimes, WAEC & JAMB PINs"],
    cta: "Pay a bill", to: "/bills",
  },
  {
    icon: Briefcase, eyebrow: "Corporate & office jobs", title: "Find jobs with verified employers",
    points: ["Office, corporate and skilled roles", "Every employer is CAC- and KYC-checked", "Apply with your CV and track each application"],
    cta: "Browse jobs", to: "/jobs",
  },
  {
    icon: Bike, eyebrow: "Dispatch & delivery", title: "Send packages, get food delivered",
    points: ["Bikes, kekes, cars and vans near you", "See the exact price before you book", "Pay in the app at pickup; code-confirmed delivery"],
    cta: "Send a package", to: "/dispatch",
  },
  {
    icon: Wrench, eyebrow: "Trusted artisans", title: "Hire vetted pros for any job",
    points: ["Plumbers, electricians, cleaners and more", "ID- and work-verified, rated by real customers", "Agree the price up front"],
    cta: "Find an artisan", to: "/artisans",
  },
];

export function ServicesSection() {
  return (
    <section id="services" className="mx-auto max-w-7xl px-5 py-20">
      <Reveal className="text-center">
        <p className="text-sm font-semibold uppercase tracking-wider text-brand-500">Everything on JobCatch</p>
        <h2 className="mt-2 font-display text-3xl font-extrabold text-brand-900 sm:text-4xl">Bills, jobs, deliveries and artisans — one account</h2>
        <p className="mx-auto mt-3 max-w-2xl text-ink/60">Switch between paying, hiring, working and sending without signing up again. Your wallet, receipts and notifications live in one place.</p>
      </Reveal>
      <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {SERVICES.map((s, i) => (
          <Reveal key={s.to} delay={i * 0.08}>
            <Link to={s.to} className="group flex h-full flex-col rounded-3xl bg-white p-6 ring-1 ring-brand-100 transition hover:-translate-y-1 hover:shadow-xl hover:ring-brand-300">
              <span className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-brand-700 to-brand-500 text-white shadow-md">
                <s.icon className="h-6 w-6" />
              </span>
              <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-brand-500">{s.eyebrow}</p>
              <h3 className="mt-1 font-display text-lg font-bold text-brand-900">{s.title}</h3>
              <ul className="mt-3 flex-1 space-y-2">
                {s.points.map((p) => (
                  <li key={p} className="flex gap-2 text-sm text-ink/60"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />{p}</li>
                ))}
              </ul>
              <span className="mt-5 inline-flex items-center gap-1 text-sm font-semibold text-brand-600 transition-all group-hover:gap-2">{s.cta}<ArrowRight className="h-4 w-4" /></span>
            </Link>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
