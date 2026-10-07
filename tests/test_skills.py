import yaml

from jobradar.config import CONFIG_DIR
from jobradar.skills import SkillTaxonomy

TAX = SkillTaxonomy(yaml.safe_load((CONFIG_DIR / "skills.yaml").read_text()))


def test_common_english_words_are_not_skills():
    text = (
        "Welcome to Meesho, where every story begins with a spark of inspiration. "
        "React to incidents quickly. Express interest. Send your CV. We are SOC 2 compliant. "
        "We raised a Series C, and you will excel in this role."
    )
    assert TAX.extract(text) == []


def test_language_boundaries():
    found = TAX.extract("Java and JavaScript, C/C++, C#, .NET Core, MySQL, NoSQL and SQL, Go, Rust")
    for s in [
        "Java",
        "JavaScript",
        "C",
        "C++",
        "C#",
        ".NET",
        "MySQL",
        "NoSQL",
        "SQL",
        "Go",
        "Rust",
    ]:
        assert s in found, s


def test_order_of_first_appearance_and_aliases():
    assert TAX.extract("k8s, then pyspark, then scikit learn") == [
        "Kubernetes",
        "Apache Spark",
        "scikit-learn",
    ]


def test_exclude_company_name():
    assert TAX.extract("Join Databricks to work on Spark", exclude={"Databricks"}) == [
        "Apache Spark"
    ]


def test_canonicalize_and_normalize_list():
    assert TAX.canonicalize("pytorch") == "PyTorch"
    assert TAX.canonicalize("K8S") == "Kubernetes"
    assert TAX.canonicalize("Rest API") == "REST APIs"
    assert TAX.canonicalize("teamwork") is None
    canon, other = TAX.normalize_list(["python", "Spark", "CIBIL bureau data", "python", "", 3])
    assert canon == ["Python", "Apache Spark"]
    assert other == ["CIBIL bureau data"]


def test_export_is_serialisable_and_complete():
    data = TAX.export()
    assert len(data["patterns"]) == len(TAX.patterns)
    assert data["categories"]["Python"] == "languages"
    assert all(set(p) == {"s", "p", "f"} for p in data["patterns"])
