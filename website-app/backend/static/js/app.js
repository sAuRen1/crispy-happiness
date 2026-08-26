// ================================
// 1. ПЛАВНЫЙ СКРОЛЛ
// ================================

document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener("click", function (e) {
        e.preventDefault();

        const target = document.querySelector(this.getAttribute("href"));

        if (target) {
            target.scrollIntoView({
                behavior: "smooth"
            });
        }
    });
});


// ================================
// 2. ПОДСВЕТКА АКТИВНОГО МЕНЮ
// ================================

const sections = document.querySelectorAll("section, div[id]");
const navLinks = document.querySelectorAll(".nav a");

window.addEventListener("scroll", () => {

    let current = "";

    sections.forEach(section => {
        const sectionTop = section.offsetTop;

        if (scrollY >= sectionTop - 150) {
            current = section.getAttribute("id");
        }
    });

    navLinks.forEach(link => {
        link.classList.remove("active");

        if (link.getAttribute("href") === "#" + current) {
            link.classList.add("active");
        }
    });

});


// ================================
// 3. АНИМАЦИЯ ПОЯВЛЕНИЯ БЛОКОВ
// ================================

const observer = new IntersectionObserver(entries => {

    entries.forEach(entry => {

        if (entry.isIntersecting) {
            entry.target.classList.add("show");
        }

    });

}, {
    threshold: 0.15
});

// FAQ сознательно не добавляем сюда: это содержательный, а не декоративный
// блок, и прятать ответы за анимацией появления — плохая идея (если IntersectionObserver
// почему-то не сработает вовремя при быстрой прокрутке, пользователь просто не увидит текст).
document.querySelectorAll(".card, .card-usligi, .why-card, .approach-step").forEach(el => {
    el.classList.add("hidden");
    observer.observe(el);
});


// ================================
// 4. КНОПКА "НАВЕРХ"
// ================================

const btn = document.createElement("button");

btn.innerText = "↑";
btn.classList.add("to-top");

document.body.appendChild(btn);

window.addEventListener("scroll", () => {
    btn.style.display = window.scrollY > 400 ? "block" : "none";
});

btn.addEventListener("click", () => {
    window.scrollTo({
        top: 0,
        behavior: "smooth"
    });
});