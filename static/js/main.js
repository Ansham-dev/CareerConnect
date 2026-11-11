// CareerConnect client-side validation

// Show/hide company field on register page based on role
document.addEventListener("DOMContentLoaded", function () {
  var form = document.querySelector("form");
  if (!form) return;

  var companyField = document.getElementById("company-field");
  var roleInputs = document.querySelectorAll('input[name="role"]');

  if (companyField && roleInputs.length) {
    var toggle = function () {
      var selected = document.querySelector('input[name="role"]:checked');
      companyField.classList.toggle("hidden", !selected || selected.value !== "employer");
      var companyInput = companyField.querySelector("input");
      if (companyInput) companyInput.required = !!selected && selected.value === "employer";
    };
    roleInputs.forEach(function (r) {
      r.addEventListener("change", toggle);
    });
    toggle();
  }

  // Password match check
  var password = form.querySelector('input[name="password"]');
  var confirm = form.querySelector('input[name="confirm"]');
  if (password && confirm) {
    form.addEventListener("submit", function (e) {
      if (password.value !== confirm.value) {
        e.preventDefault();
        alert("Passwords do not match.");
        confirm.focus();
      }
    });
  }

  // Confirmation dialogs
  document.querySelectorAll(".confirm-form").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm") || "Are you sure?")) {
        e.preventDefault();
      }
    });
  });

  // Resume file type/size validation (2 MB)
  var resume = form.querySelector('input[type="file"][accept*="pdf"]');
  if (resume) {
    form.addEventListener("submit", function (e) {
      if (!resume.files.length) return;
      var file = resume.files[0];
      var ok = /\.(pdf|doc|docx)$/i.test(file.name);
      if (!ok) {
        e.preventDefault();
        alert("Resume must be a PDF or Word document (.pdf, .doc, .docx).");
        return;
      }
      if (file.size > 2 * 1024 * 1024) {
        e.preventDefault();
        alert("Resume must be smaller than 2 MB.");
      }
    });
  }
});
