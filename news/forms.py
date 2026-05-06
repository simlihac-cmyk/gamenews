from django import forms

from .models import Franchise, NewsCategory, Source, TrustLabel


TRUST_LABEL_CHOICES_KO = (
    ("", "신뢰도 전체"),
    (TrustLabel.OFFICIAL, "공식"),
    (TrustLabel.REPORTED, "보도"),
    (TrustLabel.RUMOR, "루머"),
    (TrustLabel.UNKNOWN, "미확인"),
)

CATEGORY_CHOICES_KO = (
    ("", "카테고리 전체"),
    (NewsCategory.OFFICIAL, "공식"),
    (NewsCategory.DIRECT, "Direct"),
    (NewsCategory.RELEASE_DATE, "발매일"),
    (NewsCategory.TRAILER, "트레일러"),
    (NewsCategory.NEW_GAME, "신작"),
    (NewsCategory.UPDATE, "업데이트"),
    (NewsCategory.SALE, "세일"),
    (NewsCategory.RUMOR, "루머"),
    (NewsCategory.LEAK, "유출"),
    (NewsCategory.GENERAL, "일반"),
)


class NewsItemFilterForm(forms.Form):
    READ_CHOICES = (
        ("", "전체"),
        ("false", "읽지 않음"),
        ("true", "읽음"),
    )
    BOOKMARK_CHOICES = (
        ("", "전체"),
        ("true", "북마크"),
        ("false", "북마크 아님"),
    )

    q = forms.CharField(required=False, label="검색")
    trust_label = forms.ChoiceField(required=False, choices=TRUST_LABEL_CHOICES_KO, label="신뢰도")
    category = forms.ChoiceField(required=False, choices=CATEGORY_CHOICES_KO, label="카테고리")
    source = forms.ModelChoiceField(required=False, queryset=Source.objects.none(), label="출처")
    franchise = forms.ModelChoiceField(required=False, queryset=Franchise.objects.none(), label="프랜차이즈")
    is_read = forms.ChoiceField(required=False, choices=READ_CHOICES, label="읽음")
    is_bookmarked = forms.ChoiceField(required=False, choices=BOOKMARK_CHOICES, label="북마크")
    favorites_only = forms.BooleanField(required=False, label="관심작만 보기")
    min_importance = forms.IntegerField(required=False, min_value=0, max_value=100, label="최소 중요도")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="시작일")
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="종료일")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = Source.objects.order_by("name")
        self.fields["source"].empty_label = "출처 전체"
        self.fields["franchise"].queryset = Franchise.objects.order_by("name")
        self.fields["franchise"].empty_label = "프랜차이즈 전체"
