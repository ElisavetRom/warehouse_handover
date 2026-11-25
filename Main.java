import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;

public final class Main {
    enum RequestStatus { NEW, APPROVED, REJECTED }
    enum LeaseStatus { RESERVED, ACTIVE, CLOSED, CANCELLED }

    static final class Period {
        final LocalDate start;
        final LocalDate end;
        final long days;
        Period(String start, String end) {
            this.start = LocalDate.parse(start);
            this.end = LocalDate.parse(end);
            days = ChronoUnit.DAYS.between(this.start, this.end);
            if (days < 1 || days > 365) throw new IllegalArgumentException("Срок от 1 до 365 суток");
        }
        boolean overlaps(Period other) {
            return start.isBefore(other.end) && other.start.isBefore(end);
        }
        @Override public String toString() { return start + " → " + end; }
    }

    abstract static class RentalUnit {
        final int id;
        final String code;
        final String name;
        final int area;
        final int rate;
        RentalUnit(int id, String code, String name, int area, int rate) {
            this.id = id;
            this.code = code;
            this.name = name;
            this.area = area;
            this.rate = rate;
        }
        abstract String kind();
        long dailyPrice() { return Math.multiplyExact((long) area, rate); }
        long total(Period period) { return Math.multiplyExact(dailyPrice(), period.days); }
    }

    static final class GeneralUnit extends RentalUnit {
        final String format;
        GeneralUnit(int id, String code, String name, int area, int rate, String format) {
            super(id, code, name, area, rate);
            this.format = format;
        }
        @Override String kind() { return format; }
    }

    static final class ColdUnit extends RentalUnit {
        ColdUnit(int id, String code, String name, int area, int rate) {
            super(id, code, name, area, rate);
        }
        @Override String kind() { return "Холодильный"; }
    }

    static final class Request {
        final int id;
        final RentalUnit unit;
        final String company;
        final Period period;
        final long daily;
        final long total;
        RequestStatus status = RequestStatus.NEW;
        Request(int id, RentalUnit unit, String company, Period period) {
            this.id = id;
            this.unit = unit;
            this.company = company;
            this.period = period;
            daily = unit.dailyPrice();
            total = unit.total(period);
        }
    }

    static final class Lease {
        final int id;
        final Request request;
        LeaseStatus status = LeaseStatus.RESERVED;
        Lease(int id, Request request) {
            this.id = id;
            this.request = request;
        }
    }

    interface Repository {
        Map<Integer, RentalUnit> units();
        Map<Integer, Request> requests();
        Map<Integer, Lease> leases();
        List<Lease> leasesFor(int unitId);
        void addLease(Lease lease);
    }

    static final class MemoryRepository implements Repository {
        private final Map<Integer, RentalUnit> units = new LinkedHashMap<>();
        private final Map<Integer, Request> requests = new LinkedHashMap<>();
        private final Map<Integer, Lease> leases = new LinkedHashMap<>();
        private final Map<Integer, List<Lease>> leasesByUnit = new LinkedHashMap<>();
        @Override public Map<Integer, RentalUnit> units() { return units; }
        @Override public Map<Integer, Request> requests() { return requests; }
        @Override public Map<Integer, Lease> leases() { return leases; }
        @Override public List<Lease> leasesFor(int unitId) {
            return leasesByUnit.getOrDefault(unitId, List.of());
        }
        @Override public void addLease(Lease lease) {
            leases.put(lease.id, lease);
            leasesByUnit.computeIfAbsent(lease.request.unit.id, key -> new ArrayList<>()).add(lease);
        }
    }

    static final class RentalService {
        final Repository repo;
        RentalService(Repository repo) { this.repo = repo; }

        RentalUnit unit(int id) {
            RentalUnit unit = repo.units().get(id);
            if (unit == null) throw new IllegalArgumentException("Помещение не найдено");
            return unit;
        }

        void register(String code, String name, String kind, int area, int rate) {
            String normalized = code.strip().toUpperCase(java.util.Locale.ROOT);
            if (normalized.isEmpty() || name.isBlank() || area < 1 || area > 100000 || rate < 1 || rate > 100000)
                throw new IllegalArgumentException("Проверьте код, название, площадь и ставку");
            for (RentalUnit existing : repo.units().values()) {
                if (existing.code.equals(normalized)) throw new IllegalArgumentException("Код уже используется");
            }
            int id = repo.units().size()+1;
            RentalUnit newUnit;
            switch (kind) {
                case "Холодильный": newUnit = new ColdUnit(id, normalized, name.strip(), area, rate); break;
                case "Сухой":
                case "Отапливаемый": newUnit = new GeneralUnit(id, normalized, name.strip(), area, rate, kind); break;
                default: throw new IllegalArgumentException("Неизвестный тип помещения");
            }
            repo.units().put(id, newUnit);
        }

        boolean free(int unitId, Period period) {
            for (Lease lease : repo.leasesFor(unitId)) {
                if (lease.status != LeaseStatus.CANCELLED && period.overlaps(lease.request.period)) return false;
            }
            return true;
        }

        List<RentalUnit> catalog(int minimum, String kind) {
            if (minimum < 0) throw new IllegalArgumentException("Минимальная площадь не может быть отрицательной");
            List<RentalUnit> matches = new ArrayList<>();
            for (RentalUnit unit : repo.units().values()) {
                if (unit.area >= minimum && (kind.isEmpty() || kind.equals(unit.kind()))) matches.add(unit);
            }
            matches.sort(Comparator.comparingInt((RentalUnit item) -> item.area).thenComparingInt(item -> item.id));
            return matches;
        }

        Request submit(int unitId, String company, Period period) {
            RentalUnit unit = unit(unitId);
            company = company.strip();
            int length = company.codePointCount(0, company.length());
            if (length < 2 || length > 100) throw new IllegalArgumentException("Введите название организации");
            if (!free(unitId, period)) throw new IllegalStateException("Помещение занято");
            Request request = new Request(repo.requests().size()+1, unit, company, period);
            repo.requests().put(request.id, request);
            return request;
        }

        Lease decide(int id, boolean approve) {
            Request request = repo.requests().get(id);
            if (request == null || request.status != RequestStatus.NEW)
                throw new IllegalStateException("Заявка отсутствует или обработана");
            if (!approve) {
                request.status = RequestStatus.REJECTED;
                return null;
            }
            if (!free(request.unit.id, request.period)) throw new IllegalStateException("Период уже занят");
            Lease lease = new Lease(repo.leases().size()+1, request);
            repo.addLease(lease);
            request.status = RequestStatus.APPROVED;
            return lease;
        }

        void move(int id, String action) {
            Lease lease = repo.leases().get(id);
            if (lease == null) throw new IllegalStateException("Бронь не найдена");
            if (lease.status == LeaseStatus.RESERVED && action.equals("activate")) lease.status = LeaseStatus.ACTIVE;
            else if (lease.status == LeaseStatus.RESERVED && action.equals("cancel")) lease.status = LeaseStatus.CANCELLED;
            else if (lease.status == LeaseStatus.ACTIVE && action.equals("close")) lease.status = LeaseStatus.CLOSED;
            else throw new IllegalStateException("Недопустимое действие для состояния брони");
        }
    }

    static RentalService demo() {
        RentalService service = new RentalService(new MemoryRepository());
        service.register("WH-101","Северный терминал","Отапливаемый",420,36);
        service.register("WH-204","Склад Восток","Сухой",250,29);
        service.register("WH-315","Холодный контур","Холодильный",180,54);
        service.register("WH-402","Логопарк Запад","Сухой",660,31);
        return service;
    }

    static void check(boolean condition) {
        if (!condition) throw new AssertionError("Проверка не пройдена");
    }

    static void expectError(Class<? extends RuntimeException> type, Runnable action) {
        try {
            action.run();
        } catch (RuntimeException error) {
            if (type.isInstance(error)) return;
            throw error;
        }
        throw new AssertionError("Ожидалась ошибка " + type.getSimpleName());
    }

    static void selfTest() {
        RentalService s = demo();
        Period base = new Period("2026-12-01","2026-12-08");
        Period adjacent = new Period("2026-12-08","2026-12-09");
        check(s.catalog(300,"").size()==2 && s.catalog(0,"Холодильный").get(0).id==3);
        check(!base.overlaps(adjacent));
        expectError(IllegalArgumentException.class, () -> new Period("2026-12-01","2026-12-01"));
        expectError(IllegalArgumentException.class, () -> new Period("2026-12-01","2027-12-02"));
        expectError(IllegalArgumentException.class, () -> s.submit(1," ",base));
        Request first = s.submit(1,"ООО Север",base);
        Request second = s.submit(1,"ООО Восток",base);
        check(first.total==105840 && first.daily==15120);
        Lease lease = s.decide(first.id,true);
        check(!s.free(1,base) && s.free(1,adjacent) && s.free(2,base));
        expectError(IllegalStateException.class, () -> s.decide(second.id,true));
        check(second.status==RequestStatus.NEW && s.repo.leases().size()==1);
        expectError(IllegalStateException.class, () -> s.decide(first.id,true));
        s.move(lease.id,"cancel");
        check(s.free(1,base));
        Lease next = s.decide(second.id,true);
        s.move(next.id,"activate");
        expectError(IllegalStateException.class, () -> s.move(next.id,"cancel"));
        s.move(next.id,"close");
        check(next.status==LeaseStatus.CLOSED && !s.free(1,base));
        Request later = s.submit(1,"ООО Запад",adjacent);
        check(s.decide(later.id,false)==null && later.status==RequestStatus.REJECTED);
        expectError(IllegalArgumentException.class, () -> s.register("wh-101","Дубль","Сухой",1,1));
        s.register("WH-500","Новый склад","Сухой",300,32);
        check(s.catalog(300,"Сухой").size()==2);
        System.out.println("Java: 18 проверок пройдено");
    }

    static void execute(RentalService service, String[] args) {
        switch (args[0]) {
            case "catalog":
                Period interval = new Period(args[1],args[2]);
                int min = args.length>3 ? Integer.parseInt(args[3]) : 0;
                String kind = args.length>4 ? args[4] : "";
                for (RentalUnit u : service.catalog(min,kind)) {
                    System.out.printf("%d %s %s | %s | %d м² | %s%n",u.id,u.code,u.name,u.kind(),u.area,
                                      service.free(u.id,interval) ? "свободно" : "занято");
                }
                break;
            case "quote":
                System.out.println(service.unit(Integer.parseInt(args[1])).total(new Period(args[2],args[3])) + " руб.");
                break;
            case "submit":
                Request r = service.submit(Integer.parseInt(args[1]),args[4],new Period(args[2],args[3]));
                System.out.println("Заявка " + r.id + ", " + r.total + " руб.");
                break;
            case "approve":
                System.out.println("Бронь " + service.decide(Integer.parseInt(args[1]),true).id);
                break;
            case "reject":
                service.decide(Integer.parseInt(args[1]),false);
                System.out.println("Заявка отклонена");
                break;
            case "activate": case "close": case "cancel":
                service.move(Integer.parseInt(args[1]),args[0]);
                System.out.println("Состояние обновлено");
                break;
            case "register":
                service.register(args[1],args[5],args[2],Integer.parseInt(args[3]),Integer.parseInt(args[4]));
                System.out.println("Помещение добавлено");
                break;
            case "journal":
                for (Request item : service.repo.requests().values()) {
                    System.out.printf("Заявка %d | %s | %s | %s | %d руб. | %s%n",
                                      item.id,item.company,item.unit.code,item.period,item.total,item.status);
                }
                for (Lease item : service.repo.leases().values()) {
                    System.out.printf("Бронь %d | заявка %d | %s%n",item.id,item.request.id,item.status);
                }
                break;
            default:
                System.out.println("catalog|начало|конец[|площадь|формат], quote|склад|начало|конец");
                System.out.println("submit|склад|начало|конец|организация, approve|заявка, reject|заявка");
                System.out.println("activate|бронь, close|бронь, cancel|бронь, journal, exit");
                System.out.println("register|код|формат|площадь|ставка|название");
        }
    }

    public static void main(String[] args) {
        if (args.length==1 && args[0].equals("--test")) {
            selfTest();
            return;
        }
        RentalService service = demo();
        try (Scanner input = new Scanner(System.in, java.nio.charset.StandardCharsets.UTF_8.name())) {
            System.out.println("Складской фонд · Java. Команды разделяются символом |. help — справка.");
            while (input.hasNextLine()) {
                String line = input.nextLine().strip();
                if (line.equals("exit")) break;
                if (line.isEmpty()) continue;
                try {
                    execute(service,line.split("\\|",-1));
                } catch (RuntimeException error) {
                    System.out.println("Ошибка: " + error.getMessage());
                }
            }
        }
    }
}
