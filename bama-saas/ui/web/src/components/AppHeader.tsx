/**
 * The one piece of chrome.
 *
 * This replaces a 232px sidebar plus a title bar that between them carried the
 * brand, six links, a logout button and a page title nobody needed told twice.
 * Navigation, theme and account all live here, and the content below gets the
 * full width back.
 *
 * It reads as *floating* rather than as a header band: inset from the viewport
 * edges, rounded, translucent with a backdrop blur, so the page scrolls visibly
 * underneath it instead of disappearing behind a solid strip.
 *
 * On a phone it is a brand bar only — the destinations move to the bottom tab
 * row (see styles.css), which is the pattern every native app has already
 * trained people on, and which does not clip five Persian labels mid-word the
 * way a scrolling top strip does.
 */
import { NavLink, useNavigate } from "react-router-dom";
import {
  Activity, BarChart3, Bell, Bookmark, LogOut, Moon, Percent, Search, Sun,
  MonitorCog, Sparkles, User, Wallet,
} from "lucide-react";
import { useAuth } from "@/auth";
import { useTheme, type ThemeChoice } from "@/theme";
import { BrandMark } from "@/ui";
import { useUnreadAlerts } from "@/alerts";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

/**
 * The five destinations, in the order a buyer meets them: what is the market
 * doing, what can I afford, what is cheap, find a specific car, study one.
 *
 * Five and not seven. Two screens arrived at once — the budget shortlist and
 * the alert feed — and a tab bar with seven Persian labels clips them mid-word
 * on a phone, which is the failure the bottom row exists to avoid. So the two
 * *personal* surfaces, saved cars and alerts, moved to the icon cluster on the
 * right: they are "my stuff" rather than ways into the market, the alert badge
 * has to be visible from every screen anyway, and both read fine as icons.
 *
 * `Control` is deliberately not here either — it is an operator screen, and it
 * lives in the account menu so the main nav stays the product.
 */
export const NAV = [
  { to: "/", label: "نبض بازار", icon: Sparkles, end: true },
  { to: "/budget", label: "بودجه من", icon: Wallet, end: false },
  { to: "/deals", label: "معامله‌ها", icon: Percent, end: false },
  { to: "/explore", label: "جست‌وجو", icon: Search, end: false },
  { to: "/analyse", label: "تحلیل", icon: BarChart3, end: false },
];

const THEME_OPTIONS: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "پوسته روشن", Icon: Sun },
  { value: "system", label: "هماهنگ با سیستم", Icon: MonitorCog },
  { value: "dark", label: "پوسته تیره", Icon: Moon },
];

function ThemeToggle() {
  const { choice, setChoice } = useTheme();
  return (
    <ToggleGroup
      type="single"
      value={choice}
      // A ToggleGroup lets the only selected item be deselected, which would
      // leave the app with no theme at all. Ignoring the empty value keeps the
      // control a three-way choice rather than a set of three checkboxes.
      onValueChange={(next) => next && setChoice(next as ThemeChoice)}
      variant="outline"
      size="sm"
      aria-label="پوسته"
      className="hidden sm:flex"
    >
      {THEME_OPTIONS.map(({ value, label, Icon }) => (
        <ToggleGroupItem key={value} value={value} aria-label={label} title={label}>
          <Icon className="size-4" />
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function AccountMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { choice, setChoice } = useTheme();
  if (!user) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" aria-label="حساب کاربری">
          <User className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        {/* The signed-in identity, which the old shell fetched on every page
            load and then never showed anywhere. */}
        <DropdownMenuLabel className="font-normal">
          <span className="text-muted-foreground text-xs">وارد شده با</span>
          <span dir="ltr" className="block truncate font-mono text-xs">{user.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {/* Below sm the toggle group is hidden, so the theme has to be reachable
            from somewhere. Same three choices, as menu items. */}
        <div className="sm:hidden">
          {THEME_OPTIONS.map(({ value, label, Icon }) => (
            <DropdownMenuItem
              key={value}
              onSelect={() => setChoice(value)}
              className={choice === value ? "text-accent-foreground" : undefined}
            >
              <Icon className="size-4" /> {label}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
        </div>
        {user.is_staff && (
          <DropdownMenuItem onSelect={() => navigate("/control")}>
            <Activity className="size-4" /> کنترل خزنده
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={() => logout()}>
          <LogOut className="size-4" /> خروج
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Brand mark and wordmark, shared by the app header and the signed-out one. */
function Brand() {
  return (
    <span className="text-accent flex flex-none items-center gap-2 font-extrabold tracking-tight">
      <BrandMark size={22} />
      <span className="hidden text-[15px] md:inline">بازار خودرو باما</span>
    </span>
  );
}

/** The shell for both header variants: the floating pill and what it sits in. */
function HeaderShell({ children }: { children: React.ReactNode }) {
  return (
    <header className="sticky top-0 z-30 px-3 pt-3 pb-1">
      <div
        className={
          "border-border bg-panel/80 mx-auto flex h-14 max-w-[1600px] items-center " +
          "gap-2 rounded-2xl border px-3 shadow-sm backdrop-blur-md sm:px-4"
        }
      >
        {children}
      </div>
    </header>
  );
}

export function AppHeader() {
  return (
    <HeaderShell>
      <Brand />
      <nav aria-label="Main" className="hidden min-w-0 flex-1 items-center gap-1 lg:flex">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              "flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-medium no-underline " +
              "transition-colors hover:no-underline " +
              (isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-panel-2 hover:text-foreground")
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="ms-auto flex flex-none items-center gap-2">
        <IconLink to="/saved" label="ذخیره‌شده‌ها" Icon={Bookmark} />
        <AlertsBell />
        <ThemeToggle />
        <AccountMenu />
      </div>
    </HeaderShell>
  );
}

/** One of the two personal destinations, as an icon with a real accessible name. */
function IconLink({
  to, label, Icon, children,
}: {
  to: string;
  label: string;
  Icon: typeof Bookmark;
  children?: React.ReactNode;
}) {
  return (
    <NavLink to={to} aria-label={label} title={label} className="relative">
      {({ isActive }) => (
        <>
          <Button
            variant={isActive ? "secondary" : "outline"}
            size="icon"
            // The NavLink is the control; this is only its surface. Without
            // this the button steals the tab stop and the link becomes
            // unreachable by keyboard.
            tabIndex={-1}
            aria-hidden
          >
            <Icon className="size-4" />
          </Button>
          {children}
        </>
      )}
    </NavLink>
  );
}

/**
 * The alert feed, with its unread count.
 *
 * The badge is why this is an icon in the header rather than a sixth tab: it
 * has to be visible from every screen, and a count that only appears once you
 * are already looking at the feed tells you nothing.
 */
function AlertsBell() {
  const unread = useUnreadAlerts();
  const count = unread.data?.unread ?? 0;
  return (
    <IconLink to="/alerts" label={count ? `اعلان‌ها (${count} خوانده‌نشده)` : "اعلان‌ها"}
              Icon={Bell}>
      {count > 0 && (
        <span
          aria-hidden
          className={
            "bg-primary text-primary-foreground pointer-events-none absolute " +
            "-top-1 -end-1 grid min-w-4 place-items-center rounded-full px-1 " +
            "text-[10px] font-bold leading-4"
          }
        >
          {count > 99 ? "۹۹+" : count}
        </span>
      )}
    </IconLink>
  );
}

/**
 * The same five destinations, as a bottom tab bar, below the width where they
 * fit in the header.
 *
 * Deliberately not a hamburger: these are the product's five surfaces, not a
 * settings tree, and putting them one tap away is worth the strip of screen. The
 * safe-area padding keeps the labels clear of the home indicator on a phone.
 */
export function MobileNav() {
  return (
    <nav
      aria-label="Main"
      className={
        "border-border bg-panel fixed inset-x-0 bottom-0 z-30 flex border-t lg:hidden " +
        "pb-[env(safe-area-inset-bottom)]"
      }
    >
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            "flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-1 " +
            "px-1 py-1.5 text-[10.5px] no-underline hover:no-underline " +
            (isActive ? "text-accent-foreground" : "text-muted-foreground")
          }
        >
          <Icon className="size-4 flex-none" />
          <span className="max-w-full truncate">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * The signed-out chrome.
 *
 * Deliberately not the header bar above: the auth layout already carries the
 * brand twice and fills the viewport, so a third lockup in a full-width strip
 * would both repeat itself and push the form off the screen. This is the two
 * controls that were genuinely missing — the theme, which a signed-out visitor
 * could not reach at all, and the way across to the other auth route.
 *
 * The API is gated end to end, so there is nothing else to navigate to yet.
 */
export function AuthHeader({ to, label }: { to: string; label: string }) {
  return (
    <div className="absolute inset-x-0 top-0 z-30 flex justify-end gap-2 p-4">
      <ThemeToggle />
      <Button asChild variant="outline" size="sm">
        <NavLink to={to}>{label}</NavLink>
      </Button>
    </div>
  );
}
